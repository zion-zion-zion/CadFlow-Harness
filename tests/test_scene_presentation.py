from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import cadflow as cad

from backend.agent.prompt import _build_agent_system_prompt
from backend.cad_executor import CADExecutor
from backend.model_source import create_model_source
from backend.preview_glb import (
    assembly_preview_glb,
    shape_preview_glb,
    validate_assembly_preview_glb,
)


def _scene_manifest(project: Path) -> tuple[dict[str, object], dict[str, object]]:
    with zipfile.ZipFile(project / "artifacts" / "model.scene.zip") as archive:
        scene = json.loads(archive.read("scene.json"))
        presentation = json.loads(archive.read("presentation/presentation.json"))
    return scene, presentation


def _review_manifest(project: Path, relative_path: str) -> dict[str, object]:
    return json.loads((project / relative_path).read_text(encoding="utf-8"))


def _glb_document(payload: bytes) -> dict[str, object]:
    chunk_length, chunk_type = struct.unpack_from("<I4s", payload, 12)
    assert chunk_type == b"JSON"
    return json.loads(payload[20 : 20 + chunk_length])


def test_executor_applies_instance_presentation_to_repeated_parts(
    tmp_path: Path,
) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": [],
    "envelope": {"max_size_mm": [25.0, 10.0, 10.0]},
}

PRESENTATION = {
    "schema_version": "1.0",
    "presentation_id": "robot-finishes",
    "source_scene_id": "model",
    "appearances": [
        {
            "name": "painted-red",
            "base_color": [0.8, 0.03, 0.02, 1.0],
            "metallic": 0.7,
            "roughness": 0.22,
            "alpha_mode": "opaque",
            "double_sided": False,
            "edge_color": [0.05, 0.05, 0.05, 1.0],
        },
        {
            "name": "painted-blue",
            "base_color": [0.02, 0.12, 0.8, 1.0],
            "metallic": 0.65,
            "roughness": 0.28,
            "alpha_mode": "opaque",
            "double_sided": False,
            "edge_color": [0.03, 0.03, 0.08, 1.0],
        },
    ],
    "node_overrides": [
        {"node_id": "instance/main/standing", "appearance_name": "painted-red"},
        {"node_id": "instance/main/lying", "appearance_name": "painted-blue"},
    ],
    "cameras": [],
}

def build_model(model: cad.Model):
    robot = cad.make_part_rpart(
        part_id="robot",
        body=cad.make_box_rsolid(width=8.0, height=8.0, depth=8.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="pair")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=robot,
        component_id="standing",
        placement=cad.identity_placement_rplacement(),
    )
    return cad.add_component_rassembly(
        assembly=assembly,
        item=robot,
        component_id="lying",
        placement=cad.make_placement_rplacement(origin=(12.0, 0.0, 0.0)),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.scene_parse_result.valid is True
    scene, presentation = _scene_manifest(tmp_path)
    assert presentation["presentation_id"] == "robot-finishes"
    assert scene["presentation_source"]["embedded_artifact_uri"] == (
        "presentation/presentation.json"
    )
    assert len(scene["geometry_assets"]) == 1
    appearances = {
        appearance["appearance_id"]: appearance for appearance in scene["appearances"]
    }
    colors = {
        node["node_id"]: appearances[node["appearance_override_id"]]["base_color"]
        for node in scene["nodes"]
        if node["appearance_override_id"] is not None
    }
    assert colors == {
        "instance/main/lying": [0.02, 0.12, 0.8, 1.0],
        "instance/main/standing": [0.8, 0.03, 0.02, 1.0],
    }
    review = _review_manifest(tmp_path, result.review_manifest_path)
    assert review["appearance"]["render_mode"] == "scene_presentation"
    assert [item["base_color"] for item in review["appearance"]["components"]] == [
        [0.8, 0.03, 0.02],
        [0.02, 0.12, 0.8],
    ]


def test_executor_applies_single_shape_presentation_to_review(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRESENTATION = {
    "schema_version": "1.0",
    "presentation_id": "single-finish",
    "source_scene_id": "model",
    "appearances": [{
        "name": "green-metal",
        "base_color": [0.03, 0.72, 0.18, 1.0],
        "metallic": 0.8,
        "roughness": 0.2,
        "alpha_mode": "opaque",
        "double_sided": False,
        "edge_color": [0.01, 0.08, 0.02, 1.0],
    }],
    "node_overrides": [
        {"node_id": "instance/main", "appearance_name": "green-metal"}
    ],
    "cameras": [],
}

def build_model(model: cad.Model):
    return model.box(width=4.0, depth=5.0, height=6.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.review_evidence_error is None
    review = _review_manifest(tmp_path, result.review_manifest_path)
    assert review["appearance"]["render_mode"] == "scene_presentation"
    assert review["appearance"]["components"][0]["base_color"] == [
        0.03,
        0.72,
        0.18,
    ]


def test_executor_rejects_non_mapping_presentation(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRESENTATION = []

def build_model(model: cad.Model):
    return model.box(width=2.0, depth=3.0, height=4.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert "PRESENTATION must be a mapping" in (result.error or "")


def test_assembly_live_preview_preserves_presentation_material() -> None:
    part = cad.make_part_rpart(
        part_id="preview-part",
        body=cad.make_box_rsolid(width=4.0, height=5.0, depth=6.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="preview")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=part,
        component_id="body",
        placement=cad.identity_placement_rplacement(),
    )
    presentation = {
        "schema_version": "1.0",
        "presentation_id": "preview-finish",
        "source_scene_id": "model",
        "appearances": [
            {
                "name": "blue-metal",
                "base_color": [0.02, 0.12, 0.8, 1.0],
                "metallic": 0.82,
                "roughness": 0.18,
                "alpha_mode": "opaque",
                "double_sided": False,
                "edge_color": [0.03, 0.03, 0.08, 1.0],
            }
        ],
        "node_overrides": [
            {"node_id": "instance/main/body", "appearance_name": "blue-metal"}
        ],
        "cameras": [],
    }

    payload = assembly_preview_glb(
        assembly,
        deflection=0.5,
        presentation=presentation,
    )

    validate_assembly_preview_glb(payload)
    document = _glb_document(payload)
    materials = document["materials"]
    assert any(
        material["pbrMetallicRoughness"]
        == {
            "baseColorFactor": [0.02, 0.12, 0.8, 1.0],
            "metallicFactor": 0.82,
            "roughnessFactor": 0.18,
        }
        for material in materials
    )


def test_shape_live_preview_preserves_presentation_material() -> None:
    presentation = {
        "schema_version": "1.0",
        "presentation_id": "shape-preview-finish",
        "source_scene_id": "model",
        "appearances": [
            {
                "name": "green-metal",
                "base_color": [0.03, 0.72, 0.18, 1.0],
                "metallic": 0.8,
                "roughness": 0.2,
                "alpha_mode": "opaque",
                "double_sided": False,
                "edge_color": [0.01, 0.08, 0.02, 1.0],
            }
        ],
        "node_overrides": [
            {"node_id": "instance/main", "appearance_name": "green-metal"}
        ],
        "cameras": [],
    }
    with cad.Model() as model:
        shape = model.box(width=4.0, depth=5.0, height=6.0)
        payload = shape_preview_glb(
            shape,
            deflection=0.5,
            presentation=presentation,
        )

    validate_assembly_preview_glb(payload)
    document = _glb_document(payload)
    assert any(
        material["pbrMetallicRoughness"]
        == {
            "baseColorFactor": [0.03, 0.72, 0.18, 1.0],
            "metallicFactor": 0.8,
            "roughnessFactor": 0.2,
        }
        for material in document["materials"]
    )


def test_agent_prompt_documents_the_presentation_contract(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=tmp_path / "skills",
    )

    assert "`PRESENTATION` mapping" in prompt
    assert '`source_scene_id` must be `"model"`' in prompt
    assert "instance/main/<component_id>/..." in prompt
    assert '"presentation_id": "requested-finish"' in prompt
