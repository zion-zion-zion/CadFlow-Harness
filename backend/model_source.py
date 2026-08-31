"""Creation of the CadFlow Model Source contract used by every Agent Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MODEL_SOURCE_NAME = "model.py"
CODE_DIRECTORY_NAME = "code"
ARTIFACT_DIRECTORY_NAME = "artifacts"
SCENE_ARTIFACT_NAME = "model.scene.zip"
_LEGACY_EXCLUDED_DIRECTORIES = frozenset(
    {
        CODE_DIRECTORY_NAME,
        ARTIFACT_DIRECTORY_NAME,
        ".cad-review",
        ".git",
        "__pycache__",
        "conversation_history",
        "large_tool_results",
        "previews",
    }
)


@dataclass(frozen=True)
class ModelSourceScaffold:
    """Fixed paths and source contract for one Project's Model Source."""

    project_dir: Path
    code_dir: Path
    model_path: Path
    artifact_dir: Path
    scene_path: Path


def project_code_directory(project_dir: str | Path) -> Path:
    """Return the isolated Python source directory for a Project."""

    return Path(project_dir).expanduser().resolve() / CODE_DIRECTORY_NAME


def model_source_path(project_dir: str | Path) -> Path:
    """Return the canonical ``code/model.py`` path for a Project."""

    return project_code_directory(project_dir) / MODEL_SOURCE_NAME


def migrate_legacy_sources(project_dir: str | Path) -> Path:
    """Move pre-``code/`` Python sources into the isolated source directory.

    Migration is content-aware: identical files are deduplicated, an untouched
    empty canonical scaffold may be replaced, and conflicting edits fail
    rather than silently choosing one version.
    """

    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    code_dir = root / CODE_DIRECTORY_NAME
    if code_dir.is_symlink():
        raise ValueError("Project code directory must not be a symlink")
    code_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[tuple[Path, Path]] = []
    for legacy_path in root.rglob("*.py"):
        relative = legacy_path.relative_to(root)
        if not relative.parts or relative.parts[0] in _LEGACY_EXCLUDED_DIRECTORIES:
            continue
        if legacy_path.is_symlink():
            raise ValueError(f"Legacy Python source must not be a symlink: {relative}")
        if not legacy_path.is_file():
            raise ValueError(f"Legacy Python source must be a regular file: {relative}")
        candidates.append((legacy_path, code_dir / relative))

    # Validate every destination conflict before moving anything so one cannot
    # leave half of a legacy source tree migrated.
    replacements: list[tuple[Path, Path, bool]] = []
    for legacy_path, target in candidates:
        try:
            target.parent.resolve().relative_to(code_dir.resolve())
        except (OSError, ValueError) as exc:
            raise ValueError(f"Project code path escapes its directory: {target}") from exc
        if target.is_symlink():
            raise ValueError(f"Model Source must not be a symlink: {target}")
        if target.exists():
            if not target.is_file():
                raise ValueError(f"Model Source must be a regular file: {target}")
            legacy_content = legacy_path.read_bytes()
            current_content = target.read_bytes()
            if current_content == legacy_content:
                replacements.append((legacy_path, target, False))
            elif not current_content:
                replacements.append((legacy_path, target, True))
            else:
                raise ValueError(
                    f"Both legacy source {legacy_path.name} and {target} exist with different content"
                )
        else:
            replacements.append((legacy_path, target, True))

    for legacy_path, target, replace_target in replacements:
        target.parent.mkdir(parents=True, exist_ok=True)
        if replace_target and target.exists():
            target.unlink()
        if legacy_path.exists():
            legacy_path.replace(target)
    return code_dir


def create_model_source(
    project_dir: str | Path,
    *,
    overwrite: bool = False,
) -> ModelSourceScaffold:
    """Create the initial empty Model Source for a Project."""

    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    code_dir = migrate_legacy_sources(root)
    model_path = code_dir / MODEL_SOURCE_NAME
    artifact_dir = root / ARTIFACT_DIRECTORY_NAME
    if model_path.is_symlink():
        raise ValueError("Model Source must not be a symlink")
    if model_path.exists() and not model_path.is_file():
        raise ValueError("Model Source must be a regular file")
    if artifact_dir.is_symlink():
        raise ValueError("Project artifact directory must not be a symlink")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if overwrite or not model_path.exists():
        model_path.write_text("", encoding="utf-8")
    return ModelSourceScaffold(
        project_dir=root,
        code_dir=code_dir,
        model_path=model_path,
        artifact_dir=artifact_dir,
        scene_path=artifact_dir / SCENE_ARTIFACT_NAME,
    )


__all__ = [
    "ARTIFACT_DIRECTORY_NAME",
    "CODE_DIRECTORY_NAME",
    "MODEL_SOURCE_NAME",
    "ModelSourceScaffold",
    "SCENE_ARTIFACT_NAME",
    "create_model_source",
    "migrate_legacy_sources",
    "model_source_path",
    "project_code_directory",
]
