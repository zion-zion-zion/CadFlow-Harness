from pathlib import Path

import pytest

from backend.model_source import (
    ARTIFACT_DIRECTORY_NAME,
    CODE_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    SCENE_ARTIFACT_NAME,
    create_model_source,
)


def test_create_model_source_writes_empty_model_source(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)

    assert scaffold.code_dir == tmp_path / CODE_DIRECTORY_NAME
    assert scaffold.model_path == tmp_path / CODE_DIRECTORY_NAME / MODEL_SOURCE_NAME
    assert scaffold.artifact_dir == tmp_path / ARTIFACT_DIRECTORY_NAME
    assert scaffold.scene_path == scaffold.artifact_dir / SCENE_ARTIFACT_NAME
    assert scaffold.model_path.is_file()
    assert scaffold.artifact_dir.is_dir()

    assert scaffold.model_path.read_text(encoding="utf-8") == ""


def test_create_model_source_does_not_overwrite_existing_source(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text("custom source\n", encoding="utf-8")

    create_model_source(tmp_path)

    assert scaffold.model_path.read_text(encoding="utf-8") == "custom source\n"


def test_create_model_source_migrates_legacy_python_tree(tmp_path: Path) -> None:
    (tmp_path / "model.py").write_text("MODEL = True\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("HELPER = True\n", encoding="utf-8")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "ignored.py").write_text("runtime\n", encoding="utf-8")

    scaffold = create_model_source(tmp_path)

    assert scaffold.model_path.read_text(encoding="utf-8") == "MODEL = True\n"
    assert (scaffold.code_dir / "helper.py").read_text(encoding="utf-8") == (
        "HELPER = True\n"
    )
    assert not (tmp_path / "model.py").exists()
    assert not (tmp_path / "helper.py").exists()
    assert (tmp_path / "artifacts" / "ignored.py").exists()


def test_create_model_source_rejects_conflicting_legacy_model(tmp_path: Path) -> None:
    (tmp_path / "model.py").write_text("legacy\n", encoding="utf-8")
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "model.py").write_text("new\n", encoding="utf-8")

    with pytest.raises(ValueError, match="different content"):
        create_model_source(tmp_path)
