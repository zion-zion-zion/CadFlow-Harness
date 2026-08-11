"""Allowlisted, read-only access to the packaged CAD references."""

from __future__ import annotations

from pathlib import Path


class ReferenceContractError(ValueError):
    """Raised when a requested reference is outside the Agent tool contract."""


class ReferenceCatalog:
    """Resolve only the reference roots that are shipped with this repository."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.skill_root = self.repo_root / "skills" / "simplecadapi"
        self.examples_root = self.repo_root / "examples"

    def read_skill_entry(self) -> str:
        return self._read(self.skill_root / "SKILL.md")

    def read_api_index(self) -> str:
        return self._read(self.skill_root / "references" / "docs" / "api" / "README.md")

    def read_stdlib_index(self) -> str:
        return self._read(
            self.skill_root / "references" / "docs" / "stdlib" / "README.md"
        )

    def read_api_doc(self, api_name: str) -> tuple[str, str]:
        name = self._doc_stem(api_name)
        path = self.skill_root / "references" / "docs" / "api" / f"{name}.md"
        return self._read(path), self._relative_reference(path)

    def read_stdlib_doc(self, stdlib_name: str) -> tuple[str, str]:
        name = self._doc_stem(stdlib_name)
        path = self.skill_root / "references" / "docs" / "stdlib" / f"{name}.md"
        return self._read(path), self._relative_reference(path)

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

    def _doc_stem(self, name: str) -> str:
        if not name or Path(name).is_absolute() or "/" in name or "\\" in name:
            raise ReferenceContractError("exact reference names cannot contain a path")
        stem = name[:-3] if name.endswith(".md") else name
        if not stem or stem in {".", ".."}:
            raise ReferenceContractError("exact reference name is invalid")
        return stem

    def _relative_reference(self, path: Path) -> str:
        return path.resolve().relative_to(self.repo_root).as_posix()

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
