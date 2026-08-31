"""Durable Project facade for conversation and CAD artifact state.

``ProjectStore`` is intentionally the only persistence entry point used by
the API and run services. Metadata, diagnostics, and artifact version details
live in focused helpers, while this facade owns their consistency boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

from .agent_logging import ConversationLog
from .harnesses import AgentHarness
from .model_source import (
    ARTIFACT_DIRECTORY_NAME,
    CODE_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    create_model_source,
)
from .product_artifact import (
    PRODUCT_ARTIFACT_MANIFEST_NAME,
    ProductArtifact,
    ProductArtifactError,
    ProductArtifactStatus,
    accept_product_artifact,
    load_product_artifact,
)
from .project_artifacts import (
    ArtifactAcceptanceError,
    ArtifactVersionStore,
    CURRENT_ARTIFACT_NAME,
    DEFAULT_ARTIFACT_VERSION_LIMIT,
    acceptance_evidence,
)
from .project_diagnostics import (
    DIAGNOSTICS_NAME,
    DiagnosticsStore,
    normalized_token_usage,
    sum_token_usage,
    token_usage_from_counts,
)
from .project_metadata import (
    MAX_PROMPT_CHARS,
    PROJECT_METADATA_NAME,
    PROMPT_NAME,
    Project,
    ProjectError,
    ProjectMetadataStore,
    ProjectNotFoundError,
    ProjectState,
    ProjectStateError,
    PromptValidationError,
    _PROJECT_ID_PATTERN,
    is_project_id,
)
from .project_artifacts import _ARTIFACT_VERSION_PATTERN
from .scene_validation import validate_scene_artifact


RESTART_RECOVERY_REASON = "Agent Run was interrupted by service restart"


class ProjectStore:
    """The single locked persistence facade for Project state."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.artifact_version_limit = _artifact_version_limit()
        self._metadata = ProjectMetadataStore(self.root)
        self._diagnostics = DiagnosticsStore()
        for project_dir in self.root.iterdir():
            if project_dir.is_dir() and is_project_id(project_dir.name):
                try:
                    # Migrate legacy root-level model.py files while the
                    # Project is discovered. Runtime files remain outside the
                    # Agent's source mount.
                    self._metadata.ensure_source_layout(project_dir)
                    ConversationLog(project_dir)
                except (OSError, ValueError):
                    continue

    def create_project(self, name: str) -> Project:
        if not isinstance(name, str) or not name.strip():
            raise ProjectError("Project name must not be empty")
        clean_name = name.strip()
        with self._lock:
            while True:
                project_id = uuid.uuid4().hex
                project_dir = self.root / project_id
                try:
                    project_dir.mkdir()
                except FileExistsError:
                    continue
                break
            now = _timestamp()
            project = Project(
                project_id=project_id,
                name=clean_name,
                state=ProjectState.DRAFT,
                created_at=now,
                updated_at=now,
            )
            create_model_source(project_dir)
            ConversationLog(project_dir)
            self._write_metadata(project_dir, project)
            return project

    def list_projects(self) -> tuple[Project, ...]:
        with self._lock:
            projects: list[Project] = []
            for child in self.root.iterdir():
                if not child.is_dir() or not is_project_id(child.name):
                    continue
                try:
                    self._metadata.ensure_source_layout(child)
                    projects.append(self._read_project(child))
                except (OSError, ProjectError, json.JSONDecodeError):
                    continue
            projects.sort(key=lambda item: item.updated_at, reverse=True)
            return tuple(projects)

    def get_project(self, project_id: str) -> Project:
        project_dir = self.project_directory(project_id)
        with self._lock:
            if not project_dir.is_dir():
                raise ProjectNotFoundError(f"Project does not exist: {project_id}")
            self._metadata.ensure_source_layout(project_dir)
            return self._read_project(project_dir)

    @staticmethod
    def _ensure_source_layout(project_dir: Path) -> None:
        ProjectMetadataStore.ensure_source_layout(project_dir)

    def delete_project(self, project_id: str) -> None:
        """Permanently remove one Project directory from the Catalog."""

        raw_project_dir = self.root / project_id if is_project_id(project_id) else None
        project_dir = self.project_directory(project_id)
        with self._lock:
            if (
                raw_project_dir is None
                or raw_project_dir.is_symlink()
                or not project_dir.is_dir()
            ):
                raise ProjectNotFoundError(f"Project does not exist: {project_id}")
            shutil.rmtree(project_dir)

    def project_directory(self, project_id: str) -> Path:
        return self._metadata.project_directory(project_id)

    def submit_prompt(
        self,
        project_id: str,
        prompt: str,
        harness: AgentHarness | str = AgentHarness.DEEPAGENTS,
    ) -> Project:
        _validate_prompt(prompt)
        try:
            selected_harness = AgentHarness(harness)
        except ValueError as exc:
            raise PromptValidationError(f"unsupported Agent harness: {harness}") from exc
        project_dir = self.project_directory(project_id)
        with self._lock:
            project = self.get_project(project_id)
            if project.state == ProjectState.RUNNING:
                raise ProjectStateError(
                    f"Project {project_id} already has a Running turn"
                )
            (project_dir / PROMPT_NAME).write_text(prompt, encoding="utf-8")
            updated = _replace_project(
                project,
                state=ProjectState.RUNNING,
                updated_at=_timestamp(),
                prompt=prompt,
                failure_reason=None,
                harness=selected_harness,
            )
            self._write_metadata(project_dir, updated)
            return updated

    def mark_succeeded(
        self,
        project_id: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> Project:
        # Acceptance and publication share the same lock as the terminal
        # state transition, so concurrent finishers cannot publish two versions.
        with self._lock:
            project_dir = self.project_directory(project_id)
            project = self.get_project(project_id)
            if project.state != ProjectState.RUNNING:
                raise ProjectStateError(
                    f"Project {project_id} is {project.state}; only Running Projects can finish"
                )
            artifact_dir = project_dir / ARTIFACT_DIRECTORY_NAME
            try:
                candidate = load_product_artifact(artifact_dir)
                candidate.require_complete()
            except (OSError, ProductArtifactError) as exc:
                raise ProjectStateError(
                    "cannot mark Project Succeeded without a complete product artifact"
                ) from exc
            if candidate.status is not ProductArtifactStatus.DRAFT:
                raise ProjectStateError(
                    "cannot mark Project Succeeded without a Draft product candidate"
                )
            try:
                scene_evidence, review_evidence = _acceptance_evidence(
                    candidate,
                    diagnostics,
                )
            except ArtifactAcceptanceError as exc:
                raise ProjectStateError(str(exc)) from exc
            try:
                self._commit_artifact_version(
                    project_id,
                    scene_evidence=scene_evidence,
                    review_evidence=review_evidence,
                )
            except (OSError, ProductArtifactError) as exc:
                raise ProjectStateError(
                    "product candidate could not be promoted to Accepted"
                ) from exc
            except ValueError as exc:
                raise ProjectError(str(exc)) from exc
            return self._mark_terminal(
                project_id,
                state=ProjectState.SUCCEEDED,
                failure_reason=None,
                diagnostics=diagnostics,
            )

    def mark_failed(
        self,
        project_id: str,
        reason: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> Project:
        if not isinstance(reason, str) or not reason.strip():
            raise ProjectError("failure reason must not be empty")
        return self._mark_terminal(
            project_id,
            state=ProjectState.FAILED,
            failure_reason=reason.strip(),
            diagnostics=diagnostics,
        )

    def mark_stopped(
        self,
        project_id: str,
        reason: str = "Agent Run stopped by user",
        diagnostics: Mapping[str, Any] | None = None,
    ) -> Project:
        """Finish a Running Project as user-stopped and hide partial output."""

        if not isinstance(reason, str) or not reason.strip():
            raise ProjectError("stop reason must not be empty")
        return self._mark_terminal(
            project_id,
            state=ProjectState.STOPPED,
            failure_reason=reason.strip(),
            diagnostics=diagnostics,
        )

    def recover_interrupted_runs(self) -> tuple[Project, ...]:
        """Mark persisted Running Projects as Failed after a service restart."""

        recovered: list[Project] = []
        with self._lock:
            for project in self.list_projects():
                if project.state != ProjectState.RUNNING:
                    continue
                existing = self.read_diagnostics(project.project_id) or {}
                diagnostics = dict(existing)
                diagnostics.pop("token_usage", None)
                diagnostics.update(
                    {
                        "status": "failed",
                        "failure_reason": RESTART_RECOVERY_REASON,
                        "recovered_after_restart": True,
                        "harness": project.harness.value,
                    }
                )
                recovered.append(
                    self.mark_failed(
                        project.project_id,
                        RESTART_RECOVERY_REASON,
                        diagnostics,
                    )
                )
        return tuple(recovered)

    def scene_artifact(self, project_id: str) -> Path:
        """Return the latest validated result, including after a failed later turn."""

        with self._lock:
            project = self.get_project(project_id)
            if project.state == ProjectState.DRAFT:
                raise ProjectStateError(
                    "Scene Artifact is unavailable before the first successful turn"
                )
            try:
                return self._artifact_store(project_id).scene()
            except (ProductArtifactError, ValueError) as exc:
                raise ProjectStateError(str(exc)) from exc

    def product_artifact(self, project_id: str) -> ProductArtifact:
        """Return the latest versioned Accepted product bundle."""

        with self._lock:
            project = self.get_project(project_id)
            if project.state == ProjectState.DRAFT:
                raise ProjectStateError(
                    "Product Artifact is unavailable before the first successful turn"
                )
            try:
                return self._artifact_store(project_id).product()
            except (ProductArtifactError, ValueError) as exc:
                raise ProjectStateError(str(exc)) from exc

    def read_diagnostics(self, project_id: str) -> dict[str, Any] | None:
        """Read the bounded diagnostic record retained for a Project."""

        with self._lock:
            self.get_project(project_id)
            try:
                return self._diagnostics.read(self.project_directory(project_id))
            except ValueError as exc:
                raise ProjectError(str(exc)) from exc

    def discard_unvalidated_artifacts(self, project_id: str) -> None:
        """Discard partial output and restore the latest validated artifact version."""

        with self._lock:
            self.get_project(project_id)
            self._artifact_store(project_id).discard_unvalidated()

    def conversation_log(self, project_id: str, **kwargs: Any) -> ConversationLog:
        project_dir = self.project_directory(project_id)
        with self._lock:
            self.get_project(project_id)
            return ConversationLog(project_dir, conversation_id=project_id, **kwargs)

    def conversation_turns(self, project_id: str) -> list[dict[str, Any]]:
        return self.conversation_log(project_id).turns()

    def current_artifact_version(self, project_id: str) -> int | None:
        with self._lock:
            return self._artifact_store(project_id).current_version()

    def current_result_kind(self, project_id: str) -> str | None:
        with self._lock:
            return self._artifact_store(project_id).current_result_kind()

    def clear_conversation(self, project_id: str) -> Project:
        """Reset one non-running Project and remove its conversation and CAD data."""

        project_dir = self.project_directory(project_id)
        with self._lock:
            project = self.get_project(project_id)
            if project.state == ProjectState.RUNNING:
                raise ProjectStateError("A Running Project cannot be cleared")
            for name in (
                "conversation.jsonl",
                "agent-run.jsonl",
                PROMPT_NAME,
                DIAGNOSTICS_NAME,
                CURRENT_ARTIFACT_NAME,
                "events.jsonl",
            ):
                path = project_dir / name
                if path.is_file() or path.is_symlink():
                    path.unlink()
            for name in (
                ARTIFACT_DIRECTORY_NAME,
                CODE_DIRECTORY_NAME,
                "conversation_history",
                "large_tool_results",
                "previews",
                ".cad-review",
                "__pycache__",
            ):
                path = project_dir / name
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            create_model_source(project_dir, overwrite=True)
            ConversationLog(project_dir)
            reset = _replace_project(
                project,
                state=ProjectState.DRAFT,
                updated_at=_timestamp(),
                prompt=None,
                failure_reason=None,
                harness=AgentHarness.DEEPAGENTS,
            )
            self._write_metadata(project_dir, reset)
            return reset

    def _artifact_store(self, project_id: str) -> ArtifactVersionStore:
        return ArtifactVersionStore(
            self.project_directory(project_id),
            version_limit=self.artifact_version_limit,
        )

    def _commit_artifact_version(
        self,
        project_id: str,
        *,
        scene_evidence: Mapping[str, Any],
        review_evidence: Mapping[str, Any],
    ) -> int:
        return self._artifact_store(project_id).commit(
            scene_evidence=scene_evidence,
            review_evidence=review_evidence,
        )

    def _restore_current_artifact(self, project_id: str) -> None:
        self._artifact_store(project_id).restore_current()

    def _artifact_versions(self, project_id: str) -> list[int]:
        return self._artifact_store(project_id).versions()

    def _prune_artifact_versions(self, project_id: str, *, current: int) -> None:
        self._artifact_store(project_id).prune(current=current)

    def _mark_terminal(
        self,
        project_id: str,
        *,
        state: str,
        failure_reason: str | None,
        diagnostics: Mapping[str, Any] | None,
    ) -> Project:
        project_dir = self.project_directory(project_id)
        with self._lock:
            project = self.get_project(project_id)
            if project.state != ProjectState.RUNNING:
                raise ProjectStateError(
                    f"Project {project_id} is {project.state}; only Running Projects can finish"
                )
            updated = _replace_project(
                project,
                state=state,
                updated_at=_timestamp(),
                failure_reason=failure_reason,
            )
            self._write_metadata(project_dir, updated)
            if state in {ProjectState.FAILED, ProjectState.STOPPED}:
                self.discard_unvalidated_artifacts(project_id)
            if diagnostics is not None:
                previous_diagnostics = self.read_diagnostics(project_id)
                merged = self._diagnostics.merge(previous_diagnostics, diagnostics)
                self._diagnostics.write(project_dir, merged)
            return updated

    def _read_project(self, project_dir: Path) -> Project:
        return self._metadata.read(project_dir)

    @staticmethod
    def _write_metadata(project_dir: Path, project: Project) -> None:
        ProjectMetadataStore.write(project_dir, project)


def _validate_prompt(prompt: str) -> None:
    ProjectMetadataStore.validate_prompt(prompt)


def _merge_diagnostics(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    return DiagnosticsStore().merge(previous, current)


_sum_token_usage = sum_token_usage
_normalized_token_usage = normalized_token_usage
_token_usage_from_counts = token_usage_from_counts


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _replace_project(project: Project, **changes: Any) -> Project:
    return ProjectMetadataStore.replace(project, **changes)


def _timestamp() -> str:
    return ProjectMetadataStore.timestamp()


def _acceptance_evidence(
    candidate: ProductArtifact,
    diagnostics: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return acceptance_evidence(candidate, diagnostics)


def _artifact_version_limit() -> int:
    raw = os.environ.get(
        "CADFLOW_ARTIFACT_VERSION_LIMIT",
        str(DEFAULT_ARTIFACT_VERSION_LIMIT),
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_ARTIFACT_VERSION_LIMIT
    return value if value > 0 else DEFAULT_ARTIFACT_VERSION_LIMIT


__all__ = [
    "CURRENT_ARTIFACT_NAME",
    "DEFAULT_ARTIFACT_VERSION_LIMIT",
    "DIAGNOSTICS_NAME",
    "AgentHarness",
    "MAX_PROMPT_CHARS",
    "Project",
    "ProjectError",
    "ProjectNotFoundError",
    "ProjectState",
    "ProjectStateError",
    "ProjectStore",
    "PromptValidationError",
    "RESTART_RECOVERY_REASON",
]
