"""Durable Project, multi-turn conversation, and CAD artifact state."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .agent_logging import ConversationLog
from .harnesses import AgentHarness
from .model_source import ARTIFACT_DIRECTORY_NAME, create_model_source


MAX_PROMPT_CHARS = 32_000
PROJECT_METADATA_NAME = "project.json"
PROMPT_NAME = "prompt.txt"
DIAGNOSTICS_NAME = "diagnostics.json"
CURRENT_ARTIFACT_NAME = "current.json"
DEFAULT_ARTIFACT_VERSION_LIMIT = 10
RESTART_RECOVERY_REASON = "Agent Run was interrupted by service restart"
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_ARTIFACT_VERSION_PATTERN = re.compile(r"^v([0-9]{4,})$")


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
    """Store Project metadata, conversation state, and versioned CAD results."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.artifact_version_limit = _artifact_version_limit()
        for project_dir in self.root.iterdir():
            if project_dir.is_dir() and _PROJECT_ID_PATTERN.fullmatch(project_dir.name):
                try:
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
        project_dir = self.project_directory(project_id)
        artifact = project_dir / "artifacts" / "model.scene.zip"
        if not artifact.is_file() or artifact.is_symlink():
            raise ProjectStateError(
                "cannot mark Project Succeeded without the canonical Scene Artifact"
            )
        self._commit_artifact_version(project_id)
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

        project = self.get_project(project_id)
        if project.state == ProjectState.DRAFT:
            raise ProjectStateError(
                "Scene Artifact is unavailable before the first successful turn"
            )
        project_dir = self.project_directory(project_id)
        version = self.current_artifact_version(project_id)
        artifact = (
            project_dir
            / ARTIFACT_DIRECTORY_NAME
            / f"v{version:04d}"
            / "files"
            / "model.scene.zip"
            if version is not None
            else project_dir / ARTIFACT_DIRECTORY_NAME / "model.scene.zip"
        )
        if not artifact.is_file() or artifact.is_symlink():
            raise ProjectStateError("Succeeded Project has no canonical Scene Artifact")
        return artifact

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
        """Discard partial output and restore the latest validated artifact version."""

        artifact_dir = self.project_directory(project_id) / "artifacts"
        with self._lock:
            self.get_project(project_id)
            if artifact_dir.is_symlink():
                artifact_dir.unlink()
                return
            if not artifact_dir.is_dir():
                return
            for child in artifact_dir.iterdir():
                if _ARTIFACT_VERSION_PATTERN.fullmatch(child.name):
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            self._restore_current_artifact(project_id)

    def conversation_log(self, project_id: str, **kwargs: Any) -> ConversationLog:
        project_dir = self.project_directory(project_id)
        with self._lock:
            self.get_project(project_id)
            return ConversationLog(
                project_dir,
                conversation_id=project_id,
                **kwargs,
            )

    def conversation_turns(self, project_id: str) -> list[dict[str, Any]]:
        return self.conversation_log(project_id).turns()

    def current_artifact_version(self, project_id: str) -> int | None:
        path = self.project_directory(project_id) / CURRENT_ARTIFACT_NAME
        if not path.is_file() or path.is_symlink():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        version = payload.get("version") if isinstance(payload, Mapping) else None
        return version if isinstance(version, int) and not isinstance(version, bool) else None

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

    def _commit_artifact_version(self, project_id: str) -> int:
        project_dir = self.project_directory(project_id)
        artifact_dir = project_dir / ARTIFACT_DIRECTORY_NAME
        versions = self._artifact_versions(project_id)
        version = (versions[-1] if versions else 0) + 1
        target = artifact_dir / f"v{version:04d}"
        temporary = artifact_dir / f".v{version:04d}.{uuid.uuid4().hex}.tmp"
        files_dir = temporary / "files"
        source_dir = temporary / "source"
        files_dir.mkdir(parents=True)
        source_dir.mkdir(parents=True)
        for child in artifact_dir.iterdir():
            if child == temporary or _ARTIFACT_VERSION_PATTERN.fullmatch(child.name):
                continue
            destination = files_dir / child.name
            if child.is_symlink():
                continue
            if child.is_dir():
                shutil.copytree(child, destination)
            elif child.is_file():
                shutil.copy2(child, destination)
        excluded_roots = {
            ARTIFACT_DIRECTORY_NAME,
            "conversation_history",
            "large_tool_results",
            "previews",
            "__pycache__",
            ".git",
        }
        for source in project_dir.rglob("*.py"):
            relative = source.relative_to(project_dir)
            if source.is_symlink() or any(part in excluded_roots for part in relative.parts):
                continue
            destination = source_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        manifest = {
            "version": version,
            "created_at": _timestamp(),
            "scene": f"{ARTIFACT_DIRECTORY_NAME}/v{version:04d}/files/model.scene.zip",
        }
        _write_json(temporary / "manifest.json", manifest)
        temporary.replace(target)
        _write_json(project_dir / CURRENT_ARTIFACT_NAME, manifest)
        self._prune_artifact_versions(project_id, current=version)
        return version

    def _restore_current_artifact(self, project_id: str) -> None:
        version = self.current_artifact_version(project_id)
        if version is None:
            return
        project_dir = self.project_directory(project_id)
        version_dir = project_dir / ARTIFACT_DIRECTORY_NAME / f"v{version:04d}"
        files_dir = version_dir / "files"
        source_dir = version_dir / "source"
        artifact_dir = project_dir / ARTIFACT_DIRECTORY_NAME
        if files_dir.is_dir():
            for source in files_dir.iterdir():
                destination = artifact_dir / source.name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
        if source_dir.is_dir():
            saved_sources = {
                source.relative_to(source_dir)
                for source in source_dir.rglob("*.py")
                if source.is_file() and not source.is_symlink()
            }
            excluded_roots = {
                ARTIFACT_DIRECTORY_NAME,
                "conversation_history",
                "large_tool_results",
                "previews",
                "__pycache__",
                ".git",
            }
            for source in project_dir.rglob("*.py"):
                relative = source.relative_to(project_dir)
                if source.is_symlink() or any(
                    part in excluded_roots for part in relative.parts
                ):
                    continue
                if relative not in saved_sources:
                    source.unlink()
            for source in source_dir.rglob("*.py"):
                relative = source.relative_to(source_dir)
                destination = project_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _artifact_versions(self, project_id: str) -> list[int]:
        artifact_dir = self.project_directory(project_id) / ARTIFACT_DIRECTORY_NAME
        versions: list[int] = []
        if not artifact_dir.is_dir():
            return versions
        for child in artifact_dir.iterdir():
            match = _ARTIFACT_VERSION_PATTERN.fullmatch(child.name)
            if child.is_dir() and match:
                versions.append(int(match.group(1)))
        return sorted(versions)

    def _prune_artifact_versions(self, project_id: str, *, current: int) -> None:
        versions = self._artifact_versions(project_id)
        removable = versions[: max(0, len(versions) - self.artifact_version_limit)]
        artifact_dir = self.project_directory(project_id) / ARTIFACT_DIRECTORY_NAME
        for version in removable:
            if version != current:
                shutil.rmtree(artifact_dir / f"v{version:04d}")

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
                _write_json(
                    project_dir / DIAGNOSTICS_NAME,
                    _merge_diagnostics(previous_diagnostics, diagnostics),
                )
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


def _merge_diagnostics(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    token_usage = _sum_token_usage(
        previous.get("token_usage") if previous is not None else None,
        current.get("token_usage"),
    )
    if token_usage is not None:
        merged["token_usage"] = token_usage
    return merged


def _sum_token_usage(previous: Any, current: Any) -> dict[str, int] | None:
    previous_usage = _normalized_token_usage(previous)
    current_usage = _normalized_token_usage(current)
    if previous_usage is None:
        return current_usage
    if current_usage is None:
        return previous_usage
    return _token_usage_from_counts(
        input_tokens=(
            previous_usage["input_tokens"] + current_usage["input_tokens"]
        ),
        cached_input_tokens=(
            previous_usage["cached_input_tokens"]
            + current_usage["cached_input_tokens"]
        ),
        output_tokens=(
            previous_usage["output_tokens"] + current_usage["output_tokens"]
        ),
    )


def _normalized_token_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = _non_negative_int(value.get("input_tokens"))
    output_tokens = _non_negative_int(value.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    cached_input_tokens = min(
        _non_negative_int(value.get("cached_input_tokens")) or 0,
        input_tokens,
    )
    return _token_usage_from_counts(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )


def _token_usage_from_counts(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> dict[str, int]:
    return {
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": input_tokens - cached_input_tokens,
        "output_tokens": output_tokens,
    }


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _replace_project(project: Project, **changes: Any) -> Project:
    return replace(project, **changes)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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
