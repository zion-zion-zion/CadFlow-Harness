"""Creation of the Model Source contract used by every Agent Run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MODEL_SOURCE_NAME = "model.py"
ARTIFACT_DIRECTORY_NAME = "artifacts"
SCENE_ARTIFACT_NAME = "model.scene.zip"


_MODEL_SOURCE = '''"""Single-part SimpleCADAPI Model Source.

The Agent may replace the body of ``build_model`` and add local helpers, but
must keep one top-level model entry point, one captured final Solid, and the
canonical Scene Artifact location below.
"""

from pathlib import Path

import simplecadapi as scad


PROJECT_DIR = Path(__file__).resolve().parent
ARTIFACT_DIR = PROJECT_DIR / "artifacts"
SCENE_ARTIFACT = ARTIFACT_DIR / "model.scene.zip"


@scad.model(graph_id="model", export_dir=ARTIFACT_DIR)
def build_model() -> scad.Solid:
    """Build exactly one physical part, using millimetres by default."""

    # Replace this valid starter geometry with the requested part. Record
    # important assumptions here when the Prompt leaves dimensions implicit.
    final_solid = scad.make_box_rsolid(
        width=10.0,
        height=10.0,
        depth=10.0,
        bottom_face_center=(0.0, 0.0, 0.0),
    )
    scad.capture_result(value=final_solid)

    # Keep one small grounding fact in the model's bounded stdout.
    print("grounding: final solid volume", round(final_solid.get_volume(), 3))
    return final_solid


# The executor reads this named result after running the source as a script.
MODEL_RESULT = build_model()
FINAL_SOLID = MODEL_RESULT.value
if not isinstance(FINAL_SOLID, scad.Solid):
    raise TypeError("Model Source must return one SimpleCADAPI Solid")
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
    """Create the complete single-part Model Source scaffold for a Project.

    ``model.py`` is the only source file exposed by the current MVP contract.
    The source computes its paths from ``__file__`` so the same text remains
    valid when an Agent edits it inside a different Project directory.
    """

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
