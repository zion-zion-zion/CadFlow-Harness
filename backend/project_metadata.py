"""Project domain objects and metadata persistence helpers.

The helpers in this module deliberately do not own concurrency.  ``ProjectStore``
keeps the single lock that composes metadata, diagnostics, conversation, and
artifact operations into one persistence boundary.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .harnesses import AgentHarness
from .model_source import CODE_DIRECTORY_NAME, MODEL_SOURCE_NAME, create_model_source


MAX_PROMPT_CHARS = 32_000
PROJECT_METADATA_NAME = "project.json"
PROMPT_NAME = "prompt.txt"
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


class ProjectMetadataStore:
    """Read/write Project metadata without creating a competing lock."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def project_directory(self, project_id: str) -> Path:
        if not is_project_id(project_id):
            raise ProjectNotFoundError("invalid Project ID")
        candidate = (self.root / project_id).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProjectNotFoundError("Project is outside the catalog") from exc
        return candidate

    @staticmethod
    def ensure_source_layout(project_dir: Path) -> None:
        """Migrate only an absent or untouched canonical source scaffold."""

        code_model = project_dir / CODE_DIRECTORY_NAME / MODEL_SOURCE_NAME
        legacy_model = project_dir / MODEL_SOURCE_NAME
        if not code_model.exists() or (
            legacy_model.is_file() and code_model.is_file() and not code_model.read_bytes()
        ):
            create_model_source(project_dir, overwrite=False)

    def read(self, project_dir: Path) -> Project:
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
    def write(project_dir: Path, project: Project) -> None:
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

    @staticmethod
    def replace(project: Project, **changes: Any) -> Project:
        return replace(project, **changes)

    @staticmethod
    def timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def validate_prompt(prompt: str) -> None:
        if not isinstance(prompt, str) or not prompt.strip():
            raise PromptValidationError("Prompt must not be empty")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise PromptValidationError(
                f"Prompt exceeds the {MAX_PROMPT_CHARS}-character limit"
            )


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ProjectError(f"project metadata field is missing or invalid: {key}")
    return value


def is_project_id(value: Any) -> bool:
    return isinstance(value, str) and _PROJECT_ID_PATTERN.fullmatch(value) is not None


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "MAX_PROMPT_CHARS",
    "PROJECT_METADATA_NAME",
    "PROMPT_NAME",
    "Project",
    "ProjectError",
    "ProjectMetadataStore",
    "ProjectNotFoundError",
    "ProjectState",
    "ProjectStateError",
    "PromptValidationError",
    "is_project_id",
]
