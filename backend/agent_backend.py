"""Filesystem backend used by the primary Text-to-CAD Agent."""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import DeleteResult, EditResult, WriteResult


_PROTECTED_PROJECT_PARTS = frozenset({".git", "artifacts", "__pycache__"})
_INTERNAL_WRITE_ROOTS = frozenset({"conversation_history", "large_tool_results"})


class _ScopedWriteMixin:
    def _write_error(self, file_path: str) -> str | None:
        if self._read_only:
            return "This reference directory is read-only"
        try:
            resolved = self._resolve_path(file_path)
            relative = resolved.relative_to(self.cwd)
        except (OSError, RuntimeError, ValueError) as exc:
            return f"Path is outside the allowed root: {exc}"
        if any(part in _PROTECTED_PROJECT_PARTS for part in relative.parts):
            return "This Project path is reserved and cannot be edited by the Agent"
        if any(part == ".env" or part.startswith(".env.") for part in relative.parts):
            return "Environment files cannot be edited by the Agent"
        if relative.parts and relative.parts[0] in _INTERNAL_WRITE_ROOTS:
            return None
        if resolved.suffix != ".py":
            return "The Agent may write only model.py or Project-local Python modules"
        return None

    def write(self, file_path: str, content: str) -> WriteResult:
        error = self._write_error(file_path)
        if error is not None:
            return WriteResult(error=error)
        return super().write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        error = self._write_error(file_path)
        if error is not None:
            return EditResult(error=error)
        return super().edit(
            file_path,
            old_string,
            new_string,
            replace_all=replace_all,
        )

    def delete(self, file_path: str) -> DeleteResult:
        del file_path
        return DeleteResult(error="delete is not available to the Agent")


class ScopedFilesystemBackend(_ScopedWriteMixin, FilesystemBackend):
    """Virtual-root filesystem backend with optional read-only mode."""

    def __init__(self, root_dir: str | Path, *, read_only: bool = False) -> None:
        super().__init__(root_dir=Path(root_dir).resolve(), virtual_mode=True)
        self._read_only = read_only


class ScopedLocalShellBackend(_ScopedWriteMixin, LocalShellBackend):
    """Trusted Project backend whose shell tool is hidden from the model."""

    def __init__(self, root_dir: str | Path, *, timeout: int) -> None:
        super().__init__(
            root_dir=Path(root_dir).resolve(),
            virtual_mode=True,
            timeout=timeout,
            inherit_env=True,
        )
        self._read_only = False


def create_agent_backend(
    project_root: str | Path,
    *,
    skill_root: str | Path | None = None,
    example_root: str | Path | None = None,
    shell_timeout: int = 120,
) -> CompositeBackend:
    """Mount the Project and read-only references behind virtual path routes."""

    project_path = Path(project_root).expanduser().resolve()
    project_backend = ScopedLocalShellBackend(project_path, timeout=shell_timeout)
    routes: dict[
        str, ScopedFilesystemBackend | ScopedLocalShellBackend
    ] = {
        f"{project_path.as_posix().rstrip('/')}/": project_backend,
    }
    for root in (skill_root, example_root):
        if root is None:
            continue
        root_path = Path(root).expanduser().resolve()
        if root_path == project_path:
            continue
        routes[f"{root_path.as_posix().rstrip('/')}/"] = ScopedFilesystemBackend(
            root_path,
            read_only=True,
        )
    return CompositeBackend(default=project_backend, routes=routes)


__all__ = [
    "ScopedFilesystemBackend",
    "ScopedLocalShellBackend",
    "create_agent_backend",
]
