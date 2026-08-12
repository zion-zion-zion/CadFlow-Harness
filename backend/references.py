"""Allowlisted, read-only access to the packaged CAD references."""

from __future__ import annotations

from pathlib import Path


class ReferenceContractError(ValueError):
    """Raised when a requested reference is outside the Agent tool contract."""


class ReferenceCatalog:
    """Resolve only the reference roots that are shipped with this repository."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.skill_root = self.repo_root / "skills" / "cadflow-model-part"
        self.examples_root = self.repo_root / "examples"

    def read_skill_entry(self) -> str:
        return self._read(self.skill_root / "SKILL.md")

    def read_api_index(self) -> str:
        return self._read(self.skill_root / "references" / "public-api.md")

    def read_stdlib_index(self) -> str:
        return self._read(self.skill_root / "references" / "public-api.md")

    def read_core_index(self) -> str:
        return self._read(self.skill_root / "references" / "public-api.md")

    def list_skill_docs(self) -> tuple[str, ...]:
        root = self.skill_root.resolve()
        if not root.is_dir():
            raise ReferenceContractError("the packaged Skill directory is missing")
        paths: list[str] = []
        for path in self.skill_root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if self._is_under(resolved, root):
                paths.append(path.relative_to(self.skill_root).as_posix())
        return tuple(sorted(paths))

    def read_skill_doc(self, relative_path: str) -> tuple[str, str]:
        if not isinstance(relative_path, str) or not relative_path:
            raise ReferenceContractError("Skill document path must be relative")
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ReferenceContractError("Skill document path must stay within the Skill")
        candidate = self.skill_root / requested
        if candidate.is_symlink():
            raise ReferenceContractError("Skill document must not be a symbolic link")
        resolved = candidate.resolve()
        if not self._is_under(resolved, self.skill_root.resolve()):
            raise ReferenceContractError("Skill document path is outside the Skill")
        content = self._read(resolved)
        return content, self._relative_reference(resolved)

    def list_examples(self) -> tuple[str, ...]:
        if not self.examples_root.is_dir():
            raise ReferenceContractError("the packaged examples directory is missing")
        paths: list[str] = []
        for path in self.examples_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.examples_root)
            if any(part.startswith(".") or part == "out" for part in relative.parts):
                continue
            resolved = path.resolve()
            if not self._is_under(resolved, self.examples_root.resolve()):
                continue
            paths.append(relative.as_posix())
        return tuple(sorted(paths))

    def read_example(self, relative_path: str) -> tuple[str, str]:
        if not relative_path or Path(relative_path).is_absolute():
            raise ReferenceContractError("example path must be relative")
        candidate = self.examples_root / Path(relative_path)
        resolved = candidate.resolve()
        if not self._is_under(resolved, self.examples_root.resolve()) or not resolved.is_file():
            raise ReferenceContractError("example path is outside the packaged examples")
        return (
            self._read(resolved, allowed_root=self.examples_root),
            self._relative_reference(resolved),
        )

    def _read(self, path: Path, *, allowed_root: Path | None = None) -> str:
        resolved = path.resolve()
        root = (allowed_root or self.skill_root).resolve()
        if not self._is_under(resolved, root):
            raise ReferenceContractError("reference path is outside the packaged Skill")
        if not resolved.is_file():
            raise ReferenceContractError(f"packaged reference does not exist: {path.name}")
        try:
            return resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ReferenceContractError("packaged reference is not UTF-8 text") from exc

    def _relative_reference(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo_root).as_posix()

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
