"""Creation of the CadFlow Model Source contract used by every Agent Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MODEL_SOURCE_NAME = "model.py"
ARTIFACT_DIRECTORY_NAME = "artifacts"
SCENE_ARTIFACT_NAME = "model.scene.zip"


_MODEL_SOURCE = '''"""Single-part CadFlow Model Source entry point.

The Agent owns the Project implementation and may replace this file or add
local modules. The runner creates the Model session and calls
``build_model(model)``.
"""

import cadflow as cad


def build_model(model: cad.Model) -> cad.Shape:
    """Build the requested part and return one final CadFlow Shape."""

    raise NotImplementedError("Implement the requested part")
'''


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
    """Create the initial non-passing Model Source for a Project."""

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
        model_path.write_text(_MODEL_SOURCE, encoding="utf-8")
    return ModelSourceScaffold(
        project_dir=root,
        model_path=model_path,
        artifact_dir=artifact_dir,
        scene_path=artifact_dir / SCENE_ARTIFACT_NAME,
    )


__all__ = [
    "ARTIFACT_DIRECTORY_NAME",
    "MODEL_SOURCE_NAME",
    "ModelSourceScaffold",
    "SCENE_ARTIFACT_NAME",
    "create_model_source",
]
