"""Creation of the CadFlow Model Source contract used by every Agent Run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


MODEL_SOURCE_NAME = "model.py"
ARTIFACT_DIRECTORY_NAME = "artifacts"
SCENE_ARTIFACT_NAME = "model.scene.zip"
_EXCLUDED_SOURCE_ROOTS = frozenset(
    {
        ".cad-review",
        ".git",
        "__pycache__",
        ARTIFACT_DIRECTORY_NAME,
        "conversation_history",
        "large_tool_results",
        "previews",
    }
)


@dataclass(frozen=True)
class ModelSourceScaffold:
    """Fixed paths and source contract for one Project's Model Source."""

    project_dir: Path
    model_path: Path
    artifact_dir: Path
    scene_path: Path


def create_model_source(
    project_dir: str | Path,
    *,
    overwrite: bool = False,
) -> ModelSourceScaffold:
    """Create the initial empty Model Source for a Project."""

    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    model_path = root / MODEL_SOURCE_NAME
    artifact_dir = root / ARTIFACT_DIRECTORY_NAME
    if model_path.is_symlink():
        raise ValueError("Model Source must not be a symlink")
    if artifact_dir.is_symlink():
        raise ValueError("Project artifact directory must not be a symlink")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if overwrite or not model_path.exists():
        model_path.write_text("", encoding="utf-8")
    return ModelSourceScaffold(
        project_dir=root,
        model_path=model_path,
        artifact_dir=artifact_dir,
        scene_path=artifact_dir / SCENE_ARTIFACT_NAME,
    )


def model_source_files(project_dir: str | Path) -> tuple[Path, ...]:
    """Return the complete executable Python source set for a Project."""

    root = Path(project_dir).expanduser().resolve()
    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if path.is_file()
            and not path.is_symlink()
            and not any(
                part in _EXCLUDED_SOURCE_ROOTS
                for part in path.relative_to(root).parts
            )
        )
    )


def model_source_digest(project_dir: str | Path) -> str:
    """Hash source paths and bytes so helper changes invalidate evidence."""

    root = Path(project_dir).expanduser().resolve()
    digest = hashlib.sha256()
    for path in model_source_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def read_model_source_bundle(project_dir: str | Path) -> str:
    """Read all Project Python sources into one reviewer-friendly document."""

    root = Path(project_dir).expanduser().resolve()
    sections = []
    for path in model_source_files(root):
        relative = path.relative_to(root).as_posix()
        sections.append(f"# FILE: {relative}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


__all__ = [
    "ARTIFACT_DIRECTORY_NAME",
    "MODEL_SOURCE_NAME",
    "ModelSourceScaffold",
    "SCENE_ARTIFACT_NAME",
    "create_model_source",
    "model_source_digest",
    "model_source_files",
    "read_model_source_bundle",
]
