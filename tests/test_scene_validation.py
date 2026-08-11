from __future__ import annotations

import json
import zipfile
from pathlib import Path

from backend.scene_validation import validate_scene_artifact


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_scene_validation_rejects_missing_scene_manifest(tmp_path: Path) -> None:
    artifact = tmp_path / "missing.scene.zip"
    _write_zip(artifact, {"geometry/unused.glb": b"not a scene"})

    result = validate_scene_artifact(artifact)

    assert result.valid is False
    assert result.error == "scene.json is missing or too large"
    assert result.member_count == 1


def test_scene_validation_rejects_member_hash_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "invalid.scene.zip"
    manifest = {
        "schema_version": "1.0",
        "source": {},
        "geometry_assets": [
            {
                "asset_id": "sha256:" + "0" * 64,
                "content_hash": "sha256:" + "0" * 64,
                "uri": "geometry/sha256-" + "0" * 64 + ".glb",
                "byte_length": 3,
            }
        ],
        "edge_assets": [],
        "entity_assets": [],
    }
    _write_zip(
        artifact,
        {
            "scene.json": json.dumps(manifest).encode("utf-8"),
            "geometry/sha256-" + "0" * 64 + ".glb": b"bad",
        },
    )

    result = validate_scene_artifact(artifact)

    assert result.valid is False
    assert result.error == (
        "package member hash differs from scene.json: geometry/sha256-"
        + "0" * 64
        + ".glb"
    )
    assert result.schema_version == "1.0"
    assert result.geometry_asset_count == 1
