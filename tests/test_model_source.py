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
    assert "@scad.model(graph_id=\"model\", export_dir=ARTIFACT_DIR)" in source
    assert "def build_model()" in source
    assert "scad.capture_result(value=final_solid)" in source
    assert "MODEL_RESULT = build_model()" in source
    assert "SCENE_ARTIFACT = ARTIFACT_DIR / \"model.scene.zip\"" in source
