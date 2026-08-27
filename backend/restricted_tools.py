"""The single CAD validation boundary exposed beside Deep Agents' built-ins."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from .contracts import ToolUseRecord
from .model_source import (
    ModelSourceScaffold,
    create_model_source,
    model_source_path,
    project_code_directory,
)


class AgentModelValidator:
    """Prepare a Project model and run its canonical CAD validator."""

    def __init__(
        self,
        *,
        project_dir: str | Path,
        executor: Any | None = None,
        on_tool_use: Callable[[ToolUseRecord], None] | None = None,
        repo_root: str | Path | None = None,
    ) -> None:
        del repo_root  # Kept only for compatibility with existing integrations.
        self._project_dir = Path(project_dir).expanduser().resolve()
        self._project_dir.mkdir(parents=True, exist_ok=True)
        self._executor = executor
        self._on_tool_use = on_tool_use
        self._records: list[ToolUseRecord] = []

    @property
    def tool_use_records(self) -> tuple[ToolUseRecord, ...]:
        return tuple(self._records)

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def code_dir(self) -> Path:
        """Return the Agent-owned Python source directory."""

        return project_code_directory(self._project_dir)

    @property
    def model_path(self) -> Path:
        """Return the canonical Model Source path inside ``code/``."""

        return model_source_path(self._project_dir)

    def source_revision(self) -> str:
        """Return a deterministic revision for every Python source in ``code/``."""

        digest = hashlib.sha256()
        for source_path in sorted(self.code_dir.rglob("*.py")):
            relative_path = source_path.relative_to(self.code_dir).as_posix().encode()
            contents = source_path.read_bytes()
            digest.update(len(relative_path).to_bytes(8, "big"))
            digest.update(relative_path)
            digest.update(len(contents).to_bytes(8, "big"))
            digest.update(contents)
        return digest.hexdigest()

    def record_tool_use(self, tool_name: str, target: str) -> None:
        """Record a host-side CAD tool invocation for the run audit."""

        self._record(tool_name, target)

    def begin_run(self) -> ModelSourceScaffold:
        scaffold = create_model_source(self._project_dir, overwrite=False)
        self._record("prepare_model_source", "code/model.py")
        return scaffold

    def validate_model(
        self,
        *,
        cancellation_token: Any | None = None,
        timeout_seconds: float | None = None,
        attempt: int | None = None,
    ) -> Any:
        executor = self._executor
        if executor is None:
            from .cad_executor import CADExecutor

            executor = CADExecutor()
            self._executor = executor
        self._record("validate_model", "code/model.py")
        kwargs: dict[str, Any] = {"cancellation_token": cancellation_token}
        if timeout_seconds is not None:
            kwargs["timeout_seconds"] = timeout_seconds
        if attempt is not None:
            kwargs["attempt"] = attempt
        return executor.execute(self._project_dir, **kwargs)

    def _record(self, tool_name: str, target: str) -> None:
        record = ToolUseRecord(
            sequence=len(self._records) + 1,
            tool_name=tool_name,
            target=target,
        )
        self._records.append(record)
        if self._on_tool_use is not None:
            self._on_tool_use(record)


RestrictedAgentTools = AgentModelValidator

__all__ = ["AgentModelValidator", "RestrictedAgentTools"]
