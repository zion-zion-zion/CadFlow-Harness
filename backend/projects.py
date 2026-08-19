"""Durable Project and one-shot Prompt state for the generation boundary."""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .harnesses import AgentHarness
from .model_source import create_model_source
from .previews import (
    PreviewError,
    preview_path,
    validate_preview_glb,
)


MAX_PROMPT_CHARS = 32_000
PROJECT_METADATA_NAME = "project.json"
PROMPT_NAME = "prompt.txt"
DIAGNOSTICS_NAME = "diagnostics.json"
RESTART_RECOVERY_REASON = "Agent Run was interrupted by service restart"
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ProjectError(ValueError):
    """Base class for invalid Project operations."""


class ProjectNotFoundError(ProjectError):
    """Raised when a Project ID is not present in the catalog."""


class ProjectStateError(ProjectError):
    """Raised when an operation does not match the Project State."""


class PromptValidationError(ProjectError):
    """Raised when a Prompt cannot be accepted by a Draft Project."""


class ProjectState(str, Enum):
    """The persisted lifecycle states used by the text-to-CAD domain."""

    DRAFT = "Draft"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    STOPPED = "Stopped"

    @classmethod
    def values(cls) -> frozenset[str]:
        return frozenset(
            {
                cls.DRAFT,
                cls.RUNNING,
                cls.SUCCEEDED,
                cls.FAILED,
                cls.STOPPED,
            }
        )


@dataclass(frozen=True)
class Project:
    """A persisted Project summary and its optional complete Prompt."""

    project_id: str
    name: str
    state: str
    created_at: str
    updated_at: str
    prompt: str | None = None
    failure_reason: str | None = None
    harness: AgentHarness = AgentHarness.DEEPAGENTS

    def __post_init__(self) -> None:
        try:
            normalized_state = ProjectState(self.state)
        except ValueError as exc:
            raise ProjectError(f"unknown Project State: {self.state}") from exc
        try:
            normalized_harness = AgentHarness(self.harness)
        except ValueError as exc:
            raise ProjectError(f"unknown Agent harness: {self.harness}") from exc
        object.__setattr__(self, "state", normalized_state)
        object.__setattr__(self, "harness", normalized_harness)


class ProjectStore:
    """Store Project metadata and Prompt files under one catalog directory.

    This is intentionally a small persistence seam for issue 02. It owns the
    one-shot Prompt transition; run observation, cancellation, and the HTTP
    workspace are added by the later issues without changing this contract.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

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
            self._write_metadata(project_dir, project)
            return project

    def list_projects(self) -> tuple[Project, ...]:
        with self._lock:
            projects: list[Project] = []
            for child in self.root.iterdir():
                if not child.is_dir() or not _PROJECT_ID_PATTERN.fullmatch(child.name):
                    continue
                try:
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
            return self._read_project(project_dir)

    def delete_project(self, project_id: str) -> None:
        """Permanently remove one Project directory from the Catalog."""

        project_dir = self.project_directory(project_id)
        with self._lock:
            if not project_dir.is_dir() or project_dir.is_symlink():
                raise ProjectNotFoundError(f"Project does not exist: {project_id}")
            shutil.rmtree(project_dir)

    def project_directory(self, project_id: str) -> Path:
        if (
            not isinstance(project_id, str)
            or _PROJECT_ID_PATTERN.fullmatch(project_id) is None
        ):
            raise ProjectNotFoundError("invalid Project ID")
        candidate = (self.root / project_id).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProjectNotFoundError("Project is outside the catalog") from exc
        return candidate

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
            if project.state != ProjectState.DRAFT:
                raise ProjectStateError(
                    f"Project {project_id} is {project.state}; only Draft Projects accept a Prompt"
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
        project_dir = self.project_directory(project_id)
        artifact = project_dir / "artifacts" / "model.scene.zip"
        if not artifact.is_file() or artifact.is_symlink():
            raise ProjectStateError(
                "cannot mark Project Succeeded without the canonical Scene Artifact"
            )
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
        """Return the canonical result only for a Succeeded Project."""

        project = self.get_project(project_id)
        if project.state != ProjectState.SUCCEEDED:
            raise ProjectStateError(
                "Scene Artifact is available only for Succeeded Projects"
            )
        artifact = self.project_directory(project_id) / "artifacts" / "model.scene.zip"
        if not artifact.is_file() or artifact.is_symlink():
            raise ProjectStateError("Succeeded Project has no canonical Scene Artifact")
        return artifact

    def preview_artifact(self, project_id: str, attempt: int, revision: int) -> Path:
        """Return one validated live GLB frame for a non-Draft Project."""

        project = self.get_project(project_id)
        if project.state == ProjectState.DRAFT:
            raise ProjectStateError("Preview is available only after an Agent Run starts")
        try:
            path = preview_path(self.project_directory(project_id), attempt, revision)
            if path.is_symlink() or not path.is_file():
                raise PreviewError("preview frame is missing")
            validate_preview_glb(path.read_bytes())
        except (OSError, PreviewError) as exc:
            raise ProjectStateError("Preview frame is invalid") from exc
        return path

    def read_diagnostics(self, project_id: str) -> dict[str, Any] | None:
        """Read the bounded diagnostic record retained for a Project."""

        path = self.project_directory(project_id) / DIAGNOSTICS_NAME
        with self._lock:
            self.get_project(project_id)
            if not path.is_file():
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ProjectError("Project diagnostics must be an object")
            return value

    def discard_unvalidated_artifacts(self, project_id: str) -> None:
        """Remove partial Scene output so a Failed Project has no result file."""

        artifact_dir = self.project_directory(project_id) / "artifacts"
        with self._lock:
            self.get_project(project_id)
            if artifact_dir.is_symlink():
                artifact_dir.unlink()
                return
            if not artifact_dir.is_dir():
                return
            for child in artifact_dir.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()

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
                _write_json(project_dir / DIAGNOSTICS_NAME, diagnostics)
            return updated

    def _read_project(self, project_dir: Path) -> Project:
        metadata_path = project_dir / PROJECT_METADATA_NAME
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProjectError("project metadata must be an object")
        project_id = _required_text(data, "project_id")
        name = _required_text(data, "name")
        state = _required_text(data, "state")
        created_at = _required_text(data, "created_at")
        updated_at = _required_text(data, "updated_at")
        if project_id != project_dir.name:
            raise ProjectError("project metadata ID does not match its directory")
        prompt_path = project_dir / PROMPT_NAME
        prompt = (
            prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else None
        )
        failure_reason = data.get("failure_reason")
        if failure_reason is not None and not isinstance(failure_reason, str):
            raise ProjectError("project failure reason is invalid")
        persisted_harness = data.get("harness", AgentHarness.DEEPAGENTS.value)
        if persisted_harness not in {item.value for item in AgentHarness}:
            persisted_harness = AgentHarness.DEEPAGENTS.value
        return Project(
            project_id=project_id,
            name=name,
            state=ProjectState(state),
            created_at=created_at,
            updated_at=updated_at,
            prompt=prompt,
            failure_reason=failure_reason,
            harness=persisted_harness,
        )

    @staticmethod
    def _write_metadata(project_dir: Path, project: Project) -> None:
        metadata: dict[str, object] = {
            "project_id": project.project_id,
            "name": project.name,
            "state": project.state,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "failure_reason": project.failure_reason,
        }
        if project.state != ProjectState.DRAFT:
            metadata["harness"] = project.harness.value
        _write_json(project_dir / PROJECT_METADATA_NAME, metadata)


def _validate_prompt(prompt: str) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise PromptValidationError("Prompt must not be empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise PromptValidationError(
            f"Prompt exceeds the {MAX_PROMPT_CHARS}-character limit"
        )


def _replace_project(project: Project, **changes: Any) -> Project:
    return replace(project, **changes)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ProjectError(f"project metadata field is missing or invalid: {key}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
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
