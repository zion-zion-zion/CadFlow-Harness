from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.product_artifact import (
    PRODUCT_ARTIFACT_MANIFEST_NAME,
    PRODUCT_ARTIFACT_SCHEMA_VERSION,
    ProductArtifactError,
    ProductArtifactStatus,
    accept_product_artifact,
    load_product_artifact,
)


def _record_file(root: Path, relative_path: str, content: bytes) -> dict[str, object]:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _write_complete_part_draft(root: Path) -> dict[str, object]:
    part_file = _record_file(root, "parts/model.step", b"part-step")
    semantic = {
        "schema_version": "cadflow-semantic-model/v1",
        "result_kind": "part",
        "root": {"item_kind": "part", "item_id": "model"},
        "assembly_definitions": [],
        "part_definitions": [
            {
                "part_id": "model",
                "name": "Model",
                "material": None,
                "connectors": [],
                "body": {
                    "step_path": part_file["path"],
                    "step_sha256": part_file["sha256"],
                    "volume_mm3": 12.5,
                },
            }
        ],
    }
    bom = {
        "schema_version": "cadflow-bom/v1",
        "items": [
            {
                "part_id": "model",
                "name": "Model",
                "material": None,
                "quantity": 1,
                "component_paths": ["model"],
                "step_path": part_file["path"],
            }
        ],
    }
    assumptions = {
        "schema_version": "cadflow-assumptions/v1",
        "assumptions": ["Rated loads are outside this geometry validation."],
    }
    validation = {
        "schema_version": "cadflow-validation/v1",
        "status": "Passed",
        "checks": [
            {"check_id": "product_spec", "status": "passed"},
            {"check_id": "leaf_geometry", "status": "passed"},
            {"check_id": "step_export_replay", "status": "passed"},
            {"check_id": "envelope", "status": "not_applicable"},
        ],
        "blocking_failures": [],
    }
    files = {
        "semantic_model": _record_file(
            root,
            "model.semantic.json",
            json.dumps(semantic, sort_keys=True).encode("utf-8"),
        ),
        "scene": _record_file(root, "model.scene.zip", b"scene"),
        "product_step": _record_file(root, "model.step", b"product-step"),
        "bom": _record_file(
            root, "bom.json", json.dumps(bom, sort_keys=True).encode("utf-8")
        ),
        "validation_report": _record_file(
            root,
            "validation.json",
            json.dumps(validation, sort_keys=True).encode("utf-8"),
        ),
        "assumptions": _record_file(
            root,
            "assumptions.json",
            json.dumps(assumptions, sort_keys=True).encode("utf-8"),
        ),
        "source_snapshot": _record_file(root, "source.zip", b"source"),
    }
    manifest: dict[str, object] = {
        "schema_version": PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "result_kind": "part",
        "status": "Draft",
        "summary": {
            "component_count": 0,
            "leaf_part_count": 1,
            "unique_part_count": 1,
            "solid_count": 1,
            "volume_mm3": 12.5,
        },
        "files": files,
        "parts": [
            {
                "part_id": "model",
                "quantity": 1,
                "component_paths": ["model"],
                "file": part_file,
            }
        ],
    }
    (root / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest


def _rewrite_declared_json(
    root: Path,
    manifest: dict[str, object],
    role: str,
    payload: object,
) -> None:
    files = manifest["files"]
    assert isinstance(files, dict)
    record = files[role]
    assert isinstance(record, dict)
    relative_path = record["path"]
    assert isinstance(relative_path, str)
    files[role] = _record_file(
        root,
        relative_path,
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    )
    (root / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _accepted_assembly_validation_bytes(*, status: str = "Accepted") -> bytes:
    check_ids = (
        "product_spec",
        "leaf_geometry",
        "envelope_spec",
        "strict_constraint_solve",
        "constraint_residuals",
        "step_export_replay",
        "envelope",
        "scene_parse",
        "independent_review",
    )
    return json.dumps(
        {
            "schema_version": "cadflow-validation/v1",
            "status": status,
            "checks": [
                {"check_id": check_id, "status": "passed"}
                for check_id in check_ids
            ],
            "blocking_failures": [],
        },
        sort_keys=True,
    ).encode("utf-8")


def test_load_product_artifact_verifies_and_resolves_the_bundle(tmp_path: Path) -> None:
    scene_path = tmp_path / "model.scene.zip"
    scene_path.write_bytes(b"scene")
    manifest = {
        "schema_version": PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "result_kind": "assembly",
        "status": "Draft",
        "summary": {
            "component_count": 2,
            "leaf_part_count": 2,
            "unique_part_count": 2,
            "solid_count": 2,
            "volume_mm3": 12.5,
        },
        "files": {
            "scene": {
                "path": "model.scene.zip",
                "sha256": "4f48f7207cebf92638debb5694e4a8677350cf88a9e98e73c6c466b6b1d43890",
                "size_bytes": 5,
            }
        },
    }
    (tmp_path / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    artifact = load_product_artifact(tmp_path)

    assert artifact.result_kind == "assembly"
    assert artifact.status is ProductArtifactStatus.DRAFT
    assert artifact.summary.component_count == 2
    assert artifact.file_path("scene") == scene_path


def test_accepted_assembly_requires_the_complete_product_bundle(tmp_path: Path) -> None:
    scene_path = tmp_path / "model.scene.zip"
    scene_path.write_bytes(b"scene")
    manifest = {
        "schema_version": PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "result_kind": "assembly",
        "status": "Accepted",
        "summary": {
            "component_count": 2,
            "leaf_part_count": 2,
            "unique_part_count": 2,
            "solid_count": 2,
            "volume_mm3": 12.5,
        },
        "files": {
            "scene": {
                "path": "model.scene.zip",
                "sha256": "4f48f7207cebf92638debb5694e4a8677350cf88a9e98e73c6c466b6b1d43890",
                "size_bytes": 5,
            }
        },
        "parts": [],
    }
    (tmp_path / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ProductArtifactError, match="Accepted product is missing"):
        load_product_artifact(tmp_path)


def test_load_accepted_product_exposes_unique_part_steps(tmp_path: Path) -> None:
    def record(relative_path: str, content: bytes) -> dict[str, object]:
        (tmp_path / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative_path).write_bytes(content)
        return {
            "path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    housing_file = record("parts/housing.step", b"housing-step")
    shaft_file = record("parts/shaft.step", b"shaft-step")
    semantic = {
        "schema_version": "cadflow-semantic-model/v1",
        "result_kind": "assembly",
        "root": {"item_kind": "assembly", "item_id": "drive"},
        "assembly_definitions": [
            {
                "assembly_id": "drive",
                "components": [
                    {
                        "component_id": part_id,
                        "item_kind": "part",
                        "item_id": part_id,
                        "placement": {
                            "origin": [index * 10.0, 0.0, 0.0],
                            "x_axis": [1.0, 0.0, 0.0],
                            "y_axis": [0.0, 1.0, 0.0],
                            "z_axis": [0.0, 0.0, 1.0],
                        },
                    }
                    for index, part_id in enumerate(("housing", "shaft"))
                ],
            }
        ],
        "part_definitions": [
            {
                "part_id": part_id,
                "name": part_id.title(),
                "material": None,
                "connectors": [],
                "body": {
                    "step_path": file["path"],
                    "step_sha256": file["sha256"],
                    "volume_mm3": volume,
                },
            }
            for part_id, file, volume in (
                ("housing", housing_file, 6.0),
                ("shaft", shaft_file, 6.5),
            )
        ],
    }
    bom = {
        "schema_version": "cadflow-bom/v1",
        "items": [
            {
                "part_id": part_id,
                "name": part_id.title(),
                "material": None,
                "quantity": 1,
                "component_paths": [f"drive/{part_id}"],
                "step_path": file["path"],
            }
            for part_id, file in (
                ("housing", housing_file),
                ("shaft", shaft_file),
            )
        ],
    }
    files = {
        "semantic_model": record(
            "model.semantic.json", json.dumps(semantic).encode("utf-8")
        ),
        "scene": record("model.scene.zip", b"scene"),
        "product_step": record("model.step", b"product-step"),
        "bom": record("bom.json", json.dumps(bom).encode("utf-8")),
        "validation_report": record(
            "validation.json",
            _accepted_assembly_validation_bytes(),
        ),
        "assumptions": record(
            "assumptions.json",
            json.dumps(
                {
                    "schema_version": "cadflow-assumptions/v1",
                    "assumptions": [],
                }
            ).encode("utf-8"),
        ),
        "source_snapshot": record("source.zip", b"source"),
    }
    manifest = {
        "schema_version": PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "result_kind": "assembly",
        "status": "Accepted",
        "summary": {
            "component_count": 2,
            "leaf_part_count": 2,
            "unique_part_count": 2,
            "solid_count": 2,
            "volume_mm3": 12.5,
        },
        "files": files,
        "parts": [
            {
                    "part_id": "housing",
                    "quantity": 1,
                    "component_paths": ["drive/housing"],
                    "file": housing_file,
            },
            {
                    "part_id": "shaft",
                    "quantity": 1,
                    "component_paths": ["drive/shaft"],
                    "file": shaft_file,
            },
        ],
    }
    (tmp_path / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    artifact = load_product_artifact(tmp_path)

    assert artifact.status is ProductArtifactStatus.ACCEPTED
    assert [part.part_id for part in artifact.parts] == ["housing", "shaft"]
    assert artifact.part_file_path("shaft") == tmp_path / "parts" / "shaft.step"


def test_loader_rejects_a_false_accepted_validation_status(tmp_path: Path) -> None:
    def record(relative_path: str, content: bytes) -> dict[str, object]:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {
            "path": relative_path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    part_file = record("parts/model.step", b"part")
    semantic = {
        "schema_version": "cadflow-semantic-model/v1",
        "result_kind": "assembly",
        "root": {"item_kind": "assembly", "item_id": "drive"},
        "assembly_definitions": [
            {
                "assembly_id": "drive",
                "components": [
                    {
                        "component_id": "model",
                        "item_kind": "part",
                        "item_id": "model",
                        "placement": {
                            "origin": [0.0, 0.0, 0.0],
                            "x_axis": [1.0, 0.0, 0.0],
                            "y_axis": [0.0, 1.0, 0.0],
                            "z_axis": [0.0, 0.0, 1.0],
                        },
                    }
                ],
            }
        ],
        "part_definitions": [
            {
                "part_id": "model",
                "name": "Model",
                "material": None,
                "connectors": [],
                "body": {
                    "step_path": part_file["path"],
                    "step_sha256": part_file["sha256"],
                    "volume_mm3": 1.0,
                },
            }
        ],
    }
    bom = {
        "schema_version": "cadflow-bom/v1",
        "items": [
            {
                "part_id": "model",
                "name": "Model",
                "material": None,
                "quantity": 1,
                "component_paths": ["drive/model"],
                "step_path": part_file["path"],
            }
        ],
    }
    manifest = {
        "schema_version": PRODUCT_ARTIFACT_SCHEMA_VERSION,
        "result_kind": "assembly",
        "status": "Accepted",
        "summary": {
            "component_count": 1,
            "leaf_part_count": 1,
            "unique_part_count": 1,
            "solid_count": 1,
            "volume_mm3": 1.0,
        },
        "files": {
                "semantic_model": record(
                    "model.semantic.json", json.dumps(semantic).encode("utf-8")
                ),
            "scene": record("model.scene.zip", b"scene"),
            "product_step": record("model.step", b"step"),
                "bom": record("bom.json", json.dumps(bom).encode("utf-8")),
            "validation_report": record(
                "validation.json",
                _accepted_assembly_validation_bytes(status="Passed"),
            ),
                "assumptions": record(
                    "assumptions.json",
                    json.dumps(
                        {
                            "schema_version": "cadflow-assumptions/v1",
                            "assumptions": [],
                        }
                    ).encode("utf-8"),
                ),
            "source_snapshot": record("source.zip", b"source"),
        },
        "parts": [
            {
                    "part_id": "model",
                    "quantity": 1,
                    "component_paths": ["drive/model"],
                    "file": part_file,
            }
        ],
    }
    (tmp_path / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ProductArtifactError, match="Accepted validation report"):
        load_product_artifact(tmp_path)


def test_product_artifact_rejects_a_symlink_bundle_root(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "model.scene.zip").write_bytes(b"scene")
    (bundle / PRODUCT_ARTIFACT_MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema_version": PRODUCT_ARTIFACT_SCHEMA_VERSION,
                "result_kind": "part",
                "status": "Draft",
                "summary": {
                    "component_count": 0,
                    "leaf_part_count": 1,
                    "unique_part_count": 1,
                    "solid_count": 1,
                    "volume_mm3": 1.0,
                },
                "files": {
                    "scene": {
                        "path": "model.scene.zip",
                        "sha256": "4f48f7207cebf92638debb5694e4a8677350cf88a9e98e73c6c466b6b1d43890",
                        "size_bytes": 5,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    alias = tmp_path / "bundle-alias"
    alias.symlink_to(bundle, target_is_directory=True)

    with pytest.raises(ProductArtifactError, match="real directory"):
        load_product_artifact(alias)


def test_accept_product_artifact_records_final_automatic_gates(tmp_path: Path) -> None:
    _write_complete_part_draft(tmp_path)

    artifact = accept_product_artifact(
        tmp_path,
        scene_evidence={"valid": True, "geometry_asset_count": 1},
        review_evidence={"status": "pass", "summary": "Geometry matches."},
    )

    assert artifact.status is ProductArtifactStatus.ACCEPTED
    accepted_validation = json.loads(
        artifact.file_path("validation_report").read_text(encoding="utf-8")
    )
    assert accepted_validation["status"] == "Accepted"
    checks = {item["check_id"]: item for item in accepted_validation["checks"]}
    assert checks["scene_parse"]["status"] == "passed"
    assert checks["independent_review"]["status"] == "passed"
    assert artifact.assumptions == (
        "Rated loads are outside this geometry validation.",
    )
    assert artifact.bom[0].part_id == "model"


@pytest.mark.parametrize(
    ("role", "mutate", "message"),
    [
        (
            "semantic_model",
            lambda value: {**value, "schema_version": "wrong"},
            "semantic model schema",
        ),
        (
            "bom",
            lambda value: {
                **value,
                "items": [{**value["items"][0], "quantity": 2}],
            },
            "BOM quantity",
        ),
        (
            "assumptions",
            lambda value: {**value, "assumptions": [""]},
            "assumption",
        ),
    ],
)
def test_loader_rejects_hash_valid_but_semantically_invalid_structured_files(
    tmp_path: Path,
    role: str,
    mutate: object,
    message: str,
) -> None:
    manifest = _write_complete_part_draft(tmp_path)
    files = manifest["files"]
    assert isinstance(files, dict)
    record = files[role]
    assert isinstance(record, dict)
    relative_path = record["path"]
    assert isinstance(relative_path, str)
    payload = json.loads((tmp_path / relative_path).read_text(encoding="utf-8"))
    changed = mutate(payload)  # type: ignore[operator]
    _rewrite_declared_json(tmp_path, manifest, role, changed)

    with pytest.raises(ProductArtifactError, match=message):
        load_product_artifact(tmp_path)
