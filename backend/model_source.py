"""Creation of the CadFlow Model Source contract used by every Agent Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MODEL_SOURCE_NAME = "model.py"
ARTIFACT_DIRECTORY_NAME = "artifacts"
SCENE_ARTIFACT_NAME = "model.scene.zip"


_MODEL_SOURCE = '''"""Single-part CadFlow Model Source.

The Agent may replace ``build_model`` and add local helpers, but must keep the
CadFlow Model/Shape boundary and return exactly one physical part. The runner
creates the Model session and calls ``build_model(model)``.
"""

from pathlib import Path

import cadflow as cad


PROJECT_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = PROJECT_DIR / "artifacts"
OUTPUT_DIR = PROJECT_DIR / "outputs"


def build_model(model: cad.Model) -> cad.Shape:
    """Build exactly one physical part, using millimetres by default."""

    # Replace this valid starter geometry with the requested part. Record
    # important assumptions here when the Prompt leaves dimensions implicit.
    final_shape = model.box(width=10.0, depth=10.0, height=10.0)
    print("grounding: final shape volume", round(final_shape.volume, 3))
    return final_shape


def main() -> None:
    """Run the source independently and write a diagnostic STEP export."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with cad.Model() as model:
        final_shape = build_model(model)
        if not isinstance(final_shape, cad.Shape):
            raise TypeError("Model Source must return one CadFlow Shape")
        if final_shape.topology.get("solids") != 1:
            raise ValueError("Model Source must return exactly one solid")
        if not final_shape.volume > 0:
            raise ValueError("Model Source must return a positive-volume Shape")
        final_shape.export_step(str(OUTPUT_DIR / "model.step"))
        print("final", final_shape.describe())


if __name__ == "__main__":
    main()
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
    overwrite: bool = True,
) -> ModelSourceScaffold:
    """Create the complete single-part Model Source scaffold for a Project."""

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
