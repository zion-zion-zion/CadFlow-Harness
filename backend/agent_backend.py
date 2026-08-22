"""Filesystem backend used by the primary Text-to-CAD Agent."""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
    StateBackend,
)
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from .model_source import CODE_DIRECTORY_NAME


_PROTECTED_PROJECT_PARTS = frozenset(
    {
        ".cad-review",
        ".git",
        "__pycache__",
        "artifacts",
        "conversation_history",
        "large_tool_results",
        "previews",
    }
)
CODE_ROUTE = "/code/"
SKILLS_ROUTE = "/skills/"
INTERNAL_ROUTE = "/.agent-internal/"
_PYTHON_ONLY_ERROR = "The Agent may access only Python files inside /code"


class _ScopedWriteMixin:
    _python_only = False

    def _python_path_error(self, file_path: str) -> str | None:
        """Reject non-Python files for the physical Agent source mount."""

        if not self._python_only:
            return None
        try:
            resolved = self._resolve_path(file_path)
        except (OSError, RuntimeError, ValueError) as exc:
            return f"Path is outside the allowed root: {exc}"
        try:
            relative = resolved.relative_to(self.cwd)
        except ValueError as exc:
            return f"Path is outside the allowed root: {exc}"
        if any(part in _PROTECTED_PROJECT_PARTS for part in relative.parts):
            return "This Project path is reserved and cannot be read by the Agent"
        if any(part == ".env" or part.startswith(".env.") for part in relative.parts):
            return "Environment files cannot be read by the Agent"
        if resolved.suffix != ".py":
            return _PYTHON_ONLY_ERROR
        return None

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
        if resolved.suffix != ".py":
            return "The Agent may write only Python files inside /code"
        return None

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        error = self._python_path_error(file_path)
        if error is not None:
            return ReadResult(error=error)
        return super().read(file_path, offset=offset, limit=limit)

    def ls(self, path: str) -> LsResult:
        if self._python_only:
            try:
                resolved = self._resolve_path(path)
                relative = resolved.relative_to(self.cwd)
            except (OSError, RuntimeError, ValueError) as exc:
                return LsResult(error=f"Path is outside the allowed root: {exc}")
            if any(part in _PROTECTED_PROJECT_PARTS for part in relative.parts):
                return LsResult(error="This Project path is reserved and cannot be listed by the Agent")
        result = super().ls(path)
        if not self._python_only or result.error or result.entries is None:
            return result
        entries = [
            entry
            for entry in result.entries
            if (
                entry.get("is_dir")
                and not any(
                    part in _PROTECTED_PROJECT_PARTS
                    or part == ".env"
                    or part.startswith(".env.")
                    for part in Path(str(entry.get("path", "")).rstrip("/")).parts
                )
            )
            or self._is_visible_python_path(str(entry.get("path", "")))
        ]
        return LsResult(entries=entries)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        result = super().glob(pattern, path)
        if not self._python_only or result.error or result.matches is None:
            return result
        matches = [
            match
            for match in result.matches
            if self._is_visible_python_path(str(match.get("path", "")))
        ]
        return GlobResult(matches=matches, truncated=result.truncated)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        result = super().grep(pattern, path, glob, max_count=max_count)
        if not self._python_only or result.error or result.matches is None:
            return result
        matches = [
            match
            for match in result.matches
            if self._is_visible_python_path(str(match.get("path", "")))
        ]
        return GrepResult(matches=matches, truncated=result.truncated)

    def _is_visible_python_path(self, virtual_path: str) -> bool:
        path = Path(virtual_path)
        return path.suffix == ".py" and not any(
            part in _PROTECTED_PROJECT_PARTS
            or part == ".env"
            or part.startswith(".env.")
            for part in path.parts
        )

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
        self._python_only = True

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Keep the host shell unavailable even if a caller bypasses middleware."""

        del command, timeout
        return ExecuteResponse(
            output="Error: shell execution is disabled for the Agent workspace",
            exit_code=1,
            truncated=False,
        )


class _SafeStateBackend(StateBackend):
    """Keep internal spill files unavailable outside a graph invocation."""

    def ls(self, path: str) -> LsResult:
        try:
            return super().ls(path)
        except RuntimeError:
            return LsResult(entries=[])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            return super().read(file_path, offset=offset, limit=limit)
        except RuntimeError:
            return ReadResult(error=f"Internal Agent file is unavailable: {file_path}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        try:
            return super().grep(pattern, path, glob, max_count=max_count)
        except RuntimeError:
            return GrepResult(matches=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            return super().glob(pattern, path)
        except RuntimeError:
            return GlobResult(matches=[])

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            return super().write(file_path, content)
        except RuntimeError:
            return WriteResult(error="Internal Agent storage is unavailable")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            return super().edit(file_path, old_string, new_string, replace_all)
        except RuntimeError:
            return EditResult(error="Internal Agent storage is unavailable")

    def delete(self, file_path: str) -> DeleteResult:
        try:
            return super().delete(file_path)
        except RuntimeError:
            return DeleteResult(error="Internal Agent storage is unavailable")


class _DeniedBackend(BackendProtocol):
    """Default route that prevents accidental fall-through into host paths."""

    _ERROR = "Path is outside the Agent workspace"

    def ls(self, path: str) -> LsResult:
        if path == "/":
            return LsResult(entries=[])
        return LsResult(error=f"{self._ERROR}: {path}")

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        del offset, limit
        return ReadResult(error=f"{self._ERROR}: {file_path}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        del pattern, path, glob, max_count
        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        del pattern, path
        return GlobResult(matches=[])

    def write(self, file_path: str, content: str) -> WriteResult:
        del content
        return WriteResult(error=f"{self._ERROR}: {file_path}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        del old_string, new_string, replace_all
        return EditResult(error=f"{self._ERROR}: {file_path}")

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=f"{self._ERROR}: {file_path}")


class _AgentCompositeBackend(CompositeBackend):
    """Composite routing with internal spill storage hidden from directory lists."""

    def ls(self, path: str) -> LsResult:
        result = super().ls(path)
        if path != "/" or result.error or result.entries is None:
            return result
        return LsResult(
            entries=[
                entry
                for entry in result.entries
                if entry.get("path") != INTERNAL_ROUTE
            ]
        )


def create_agent_backend(
    project_root: str | Path,
    *,
    skill_root: str | Path | None = None,
    shell_timeout: int = 120,
) -> CompositeBackend:
    """Mount ``project/code`` and read-only Skill references behind virtual routes."""

    project_path = Path(project_root).expanduser().resolve()
    code_path = project_path / CODE_DIRECTORY_NAME
    if code_path.is_symlink():
        raise ValueError("Project code directory must not be a symlink")
    if code_path.exists() and not code_path.is_dir():
        raise ValueError("Project code directory must be a directory")
    code_backend = ScopedLocalShellBackend(code_path, timeout=shell_timeout)
    routes: dict[str, BackendProtocol] = {
        CODE_ROUTE: code_backend,
        INTERNAL_ROUTE: _SafeStateBackend(),
    }
    if skill_root is not None:
        skill_path = Path(skill_root).expanduser().resolve()
        if skill_path != code_path:
            routes[SKILLS_ROUTE] = ScopedFilesystemBackend(
                skill_path,
                read_only=True,
            )
    return _AgentCompositeBackend(
        default=_DeniedBackend(),
        routes=routes,
        artifacts_root=INTERNAL_ROUTE.rstrip("/"),
    )


__all__ = [
    "ScopedFilesystemBackend",
    "ScopedLocalShellBackend",
    "CODE_ROUTE",
    "INTERNAL_ROUTE",
    "SKILLS_ROUTE",
    "create_agent_backend",
]
