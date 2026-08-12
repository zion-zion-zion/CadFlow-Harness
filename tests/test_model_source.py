from pathlib import Path

from backend.model_source import (
    ARTIFACT_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    SCENE_ARTIFACT_NAME,
    create_model_source,
)


def test_create_model_source_writes_complete_single_part_scaffold(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)

    assert scaffold.model_path == tmp_path / MODEL_SOURCE_NAME
    assert scaffold.artifact_dir == tmp_path / ARTIFACT_DIRECTORY_NAME
    assert scaffold.scene_path == scaffold.artifact_dir / SCENE_ARTIFACT_NAME
    assert scaffold.model_path.is_file()
    assert scaffold.artifact_dir.is_dir()

    source = scaffold.model_path.read_text(encoding="utf-8")
    assert "import cadflow as cad" in source
    assert "def build_model(model: cad.Model) -> cad.Shape" in source
    assert "return final_shape" in source
    assert "with cad.Model() as model" in source
    assert "final_shape.export_step" in source
    assert "simplecadapi" not in source.lower()
