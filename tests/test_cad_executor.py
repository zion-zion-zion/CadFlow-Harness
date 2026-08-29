from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import cadflow as cad

from backend import cad_executor, cad_runner
from backend.cad_executor import (
    CADExecutor,
    CancellationToken,
    build_cad_environment,
)
from backend.model_source import create_model_source
from backend.product_artifact import (
    ACCEPTED_PRODUCT_FILE_ROLES,
    ProductArtifactStatus,
    load_product_artifact,
)


def test_cancellation_token_preserves_the_first_cancellation_reason() -> None:
    caller = CancellationToken()
    caller.cancel()
    caller.cancel(reason="timeout")
    assert caller.cancellation_reason == "caller"

    timeout = CancellationToken()
    timeout.cancel(reason="timeout")
    timeout.cancel()
    assert timeout.cancellation_reason == "timeout"


def test_cad_execution_returns_validated_scene_facts(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    (scaffold.code_dir / "dimensions.py").write_text(
        "BOX_SIZE = 10.0\n",
        encoding="utf-8",
    )
    scaffold.model_path.write_text(
        """from dimensions import BOX_SIZE
import cadflow as cad

def build_model(model: cad.Model):
    return model.box(width=BOX_SIZE, depth=BOX_SIZE, height=BOX_SIZE)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.result_kind == "part"
    assert result.exit_code == 0
    assert result.final_shape_count == 1
    assert result.solid_count == 1
    assert result.solid_volume is not None
    assert math.isfinite(result.solid_volume) and result.solid_volume > 0
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is True
    assert result.scene_parse_result.glb_asset_count == 2
    assert result.scene_parse_result.model_json_present is False
    assert "model.scene.zip" in result.artifact_entries
    assert "product.json" in result.artifact_entries
    assert result.unique_part_count == 1
    assert result.product_status == "Draft"
    assert result.preflight_status == "passed"
    assert result.error_type is None
    assert "cadflow" in result.imported_modules
    assert "dimensions" in result.imported_modules


def test_cad_execution_accepts_a_multi_part_assembly(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    housing = cad.make_part_rpart(
        part_id="housing",
        body=cad.make_box_rsolid(width=10.0, height=4.0, depth=6.0),
        name="Housing",
    )
    shaft = cad.make_part_rpart(
        part_id="shaft",
        body=cad.make_cylinder_rsolid(radius=1.0, height=12.0),
        name="Shaft",
    )
    assembly = cad.make_assembly_rassembly(assembly_id="drive", name="Drive")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=housing,
        component_id="housing",
        placement=cad.identity_placement_rplacement(),
    )
    return cad.add_component_rassembly(
        assembly=assembly,
        item=shaft,
        component_id="shaft",
        placement=cad.make_placement_rplacement(origin=(5.0, 2.0, 0.0)),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.result_kind == "assembly"
    assert result.final_shape_count == 1
    assert result.component_count == 2
    assert result.leaf_part_count == 2
    assert result.solid_count == 2
    assert result.solid_volume is not None and result.solid_volume > 0
    assert result.scene_parse_result.valid is True
    assert result.scene_parse_result.geometry_asset_count == 2
    assert result.product_manifest_path == "artifacts/product.json"
    assert result.product_status == "Draft"
    assert result.unique_part_count == 2
    product = load_product_artifact(tmp_path / "artifacts")
    assert product.status is ProductArtifactStatus.DRAFT
    assert set(product.files) == set(ACCEPTED_PRODUCT_FILE_ROLES)
    assert [part.part_id for part in product.parts] == ["housing", "shaft"]
    assert product.file_path("product_step").is_file()


def test_cad_execution_preserves_a_single_leaf_assembly(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    seed = cad.make_part_rpart(
        part_id="seed",
        body=cad.make_box_rsolid(width=2.0, height=3.0, depth=4.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="partial")
    return cad.add_component_rassembly(
        assembly=assembly,
        item=seed,
        component_id="seed",
        placement=cad.identity_placement_rplacement(),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.result_kind == "assembly"
    assert result.component_count == result.leaf_part_count == result.solid_count == 1


def test_assembly_without_an_envelope_spec_remains_draft(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    part = cad.make_part_rpart(
        part_id="seed",
        body=cad.make_box_rsolid(width=2.0, height=3.0, depth=4.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="partial")
    return cad.add_component_rassembly(
        assembly=assembly,
        item=part,
        component_id="seed",
        placement=cad.identity_placement_rplacement(),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.product_validation_status == "Draft"
    assert any(
        "PRODUCT_SPEC.envelope" in failure
        for failure in result.product_validation_failures
    )


def test_malformed_product_spec_becomes_an_actionable_draft(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {"assumptions": [""]}

def build_model(model: cad.Model):
    return model.box(width=2.0, depth=3.0, height=4.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.product_validation_status == "Draft"
    assert result.product_validation_failures == (
        "PRODUCT_SPEC assumptions must be non-empty strings",
    )
    validation = json.loads(
        (tmp_path / "artifacts" / "validation.json").read_text(encoding="utf-8")
    )
    product_spec = next(
        item for item in validation["checks"] if item["check_id"] == "product_spec"
    )
    assert product_spec["status"] == "failed"
    assert "non-empty strings" in product_spec["message"]


def test_assembly_passes_all_deterministic_product_checks(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": ["Loads and manufacturing tolerances are out of scope."],
    "envelope": {"max_size_mm": [15.0, 5.0, 5.0]},
    "collision_exclusions": [],
}

def connector(connector_id, origin):
    return cad.make_placement_connector_rconnector(
        connector_id=connector_id,
        placement=cad.make_placement_rplacement(origin=origin),
    )

def ref(component_id, connector_id):
    return cad.make_connector_ref_rconnectorref(
        component_id=component_id,
        connector_id=connector_id,
    )

def build_model(model: cad.Model):
    housing = cad.make_part_rpart(
        part_id="housing",
        body=cad.make_box_rsolid(width=2.0, height=2.0, depth=2.0),
    )
    housing = cad.add_connector_rpart(
        part=housing,
        connector=connector("shaft_mount", (10.0, 0.0, 0.0)),
    )
    shaft = cad.make_part_rpart(
        part_id="shaft",
        body=cad.make_box_rsolid(width=2.0, height=2.0, depth=2.0),
    )
    shaft = cad.add_connector_rpart(
        part=shaft,
        connector=connector("mount", (0.0, 0.0, 0.0)),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="drive")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=housing,
        component_id="housing",
        placement=cad.identity_placement_rplacement(),
    )
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=shaft,
        component_id="shaft",
        placement=cad.make_placement_rplacement(origin=(10.0, 0.0, 0.0)),
    )
    assembly = cad.ground_component_rassembly(
        assembly=assembly,
        component_id="housing",
    )
    return cad.add_fixed_constraint_rassembly(
        assembly=assembly,
        constraint_id="shaft_mount",
        connector_a=ref("housing", "shaft_mount"),
        connector_b=ref("shaft", "mount"),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.product_validation_status == "Passed"
    assert result.product_validation_failures == ()
    validation = json.loads(
        (tmp_path / "artifacts" / "validation.json").read_text(encoding="utf-8")
    )
    checks = {item["check_id"]: item for item in validation["checks"]}
    for check_id in (
        "strict_constraint_solve",
        "constraint_residuals",
        "step_export_replay",
        "envelope",
        "current_pose_collision",
    ):
        assert checks[check_id]["status"] == "passed"
    assert checks["current_pose_collision"]["evidence"][
        "max_allowed_penetration_mm"
    ] == 0.02
    assert checks["current_pose_collision"]["evidence"]["checked_pair_count"] == 1
    feedback = {
        item["check_id"]: item for item in result.product_validation_checks
    }
    residual_ids = {
        item["constraint_id"]
        for item in feedback["constraint_residuals"]["evidence"]["residuals"]
    }
    assert "shaft_mount" in residual_ids


def test_colliding_assembly_cannot_pass_product_validation(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": [],
    "envelope": {"max_size_mm": [10.0, 10.0, 10.0]},
    "collision_exclusions": [],
}

def build_model(model: cad.Model):
    first = cad.make_part_rpart(
        part_id="first",
        body=cad.make_box_rsolid(width=4.0, height=4.0, depth=4.0),
    )
    second = cad.make_part_rpart(
        part_id="second",
        body=cad.make_box_rsolid(width=4.0, height=4.0, depth=4.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="collision")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=first,
        component_id="first",
        placement=cad.identity_placement_rplacement(),
    )
    return cad.add_component_rassembly(
        assembly=assembly,
        item=second,
        component_id="second",
        placement=cad.make_placement_rplacement(origin=(1.0, 0.0, 0.0)),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.validation_short_circuited is True
    assert result.product_validation_status == "Draft"
    assert result.product_status == "Draft"
    assert any("collision" in failure for failure in result.product_validation_failures)
    assert result.product_manifest_path is None
    assert result.artifact_entries == ()
    assert result.scene_artifact_exists is False
    assert result.review_artifact_dir is None
    assert result.review_manifest_path is None
    feedback = {
        item["check_id"]: item for item in result.product_validation_checks
    }
    collision = feedback["current_pose_collision"]
    assert collision["status"] == "failed"
    assert collision["evidence"]["failed_pair_count"] == 1
    assert collision["evidence"]["failures"][0]["penetration_depth_mm"] > 0.02
    collision_feedback = feedback["current_pose_collision"]
    assert collision_feedback["status"] == "failed"
    failure = collision_feedback["evidence"]["failures"][0]
    assert failure["component_a"] == ["first"]
    assert failure["component_b"] == ["second"]
    assert failure["allowed_penetration_mm"] == 0.02
    assert failure["penetration_depth_mm"] > 0.02
    assert failure["contacts"]
    assert len(failure["contacts"][0]["position_mm"]) == 3


def test_justified_pair_collision_exclusion_is_auditable(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": ["The overlap represents a specified press fit."],
    "envelope": {"max_size_mm": [10.0, 10.0, 10.0]},
    "collision_exclusions": [
        {
            "component_a": "fit/outer",
            "component_b": "fit/inner",
            "reason": "Specified press-fit interface",
        }
    ],
}

def build_model(model: cad.Model):
    outer = cad.make_part_rpart(
        part_id="outer",
        body=cad.make_box_rsolid(width=4.0, height=4.0, depth=4.0),
    )
    inner = cad.make_part_rpart(
        part_id="inner",
        body=cad.make_box_rsolid(width=2.0, height=2.0, depth=2.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="fit")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=outer,
        component_id="outer",
        placement=cad.identity_placement_rplacement(),
    )
    return cad.add_component_rassembly(
        assembly=assembly,
        item=inner,
        component_id="inner",
        placement=cad.make_placement_rplacement(origin=(1.0, 1.0, 1.0)),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.product_validation_status == "Passed"
    validation = json.loads(
        (tmp_path / "artifacts" / "validation.json").read_text(encoding="utf-8")
    )
    collision = next(
        item for item in validation["checks"] if item["check_id"] == "current_pose_collision"
    )
    assert collision["evidence"]["checked_pair_count"] == 0
    assert collision["evidence"]["expected_pair_count"] == 0
    assert collision["evidence"]["exclusions"] == [
        {
            "component_a": "outer",
            "component_b": "inner",
            "reason": "Specified press-fit interface",
        }
    ]


def test_assembly_outside_its_declared_envelope_remains_draft(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": [],
    "envelope": {"max_size_mm": [3.0, 3.0, 3.0]},
    "collision_exclusions": [],
}

def build_model(model: cad.Model):
    part = cad.make_part_rpart(
        part_id="oversize",
        body=cad.make_box_rsolid(width=4.0, height=2.0, depth=2.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="oversize")
    return cad.add_component_rassembly(
        assembly=assembly,
        item=part,
        component_id="oversize",
        placement=cad.identity_placement_rplacement(),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.product_validation_status == "Draft"
    assert any("exceeds" in failure for failure in result.product_validation_failures)
    validation = json.loads(
        (tmp_path / "artifacts" / "validation.json").read_text(encoding="utf-8")
    )
    envelope = next(
        item for item in validation["checks"] if item["check_id"] == "envelope"
    )
    assert envelope["status"] == "failed"
    assert envelope["evidence"]["actual_size_mm"] == [4.0, 2.0, 2.0]


def test_unsolved_assembly_cannot_pass_product_validation(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": [],
    "envelope": {"max_size_mm": [20.0, 5.0, 5.0]},
    "collision_exclusions": [],
}

def connector(connector_id, origin):
    return cad.make_placement_connector_rconnector(
        connector_id=connector_id,
        placement=cad.make_placement_rplacement(origin=origin),
    )

def ref(component_id, connector_id):
    return cad.make_connector_ref_rconnectorref(
        component_id=component_id,
        connector_id=connector_id,
    )

def build_model(model: cad.Model):
    fixed = cad.make_part_rpart(
        part_id="fixed",
        body=cad.make_box_rsolid(width=1.0, height=1.0, depth=1.0),
    )
    fixed = cad.add_connector_rpart(
        part=fixed,
        connector=connector("zero", (0.0, 0.0, 0.0)),
    )
    fixed = cad.add_connector_rpart(
        part=fixed,
        connector=connector("ten", (10.0, 0.0, 0.0)),
    )
    moving = cad.make_part_rpart(
        part_id="moving",
        body=cad.make_box_rsolid(width=1.0, height=1.0, depth=1.0),
    )
    moving = cad.add_connector_rpart(
        part=moving,
        connector=connector("origin", (0.0, 0.0, 0.0)),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="contradiction")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=fixed,
        component_id="fixed",
        placement=cad.identity_placement_rplacement(),
    )
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=moving,
        component_id="moving",
        placement=cad.make_placement_rplacement(origin=(5.0, 0.0, 0.0)),
    )
    assembly = cad.ground_component_rassembly(
        assembly=assembly,
        component_id="fixed",
    )
    assembly = cad.add_fixed_constraint_rassembly(
        assembly=assembly,
        constraint_id="at_zero",
        connector_a=ref("fixed", "zero"),
        connector_b=ref("moving", "origin"),
    )
    return cad.add_fixed_constraint_rassembly(
        assembly=assembly,
        constraint_id="at_ten",
        connector_a=ref("fixed", "ten"),
        connector_b=ref("moving", "origin"),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.validation_short_circuited is True
    assert result.product_validation_status == "Draft"
    assert result.product_status == "Draft"
    assert any("strict" in failure for failure in result.product_validation_failures)
    assert result.product_manifest_path is None
    assert result.artifact_entries == ()
    assert result.scene_artifact_exists is False
    assert result.review_artifact_dir is None
    assert result.review_manifest_path is None
    feedback = {
        item["check_id"]: item for item in result.product_validation_checks
    }
    solve = feedback["strict_constraint_solve"]
    residuals = feedback["constraint_residuals"]
    assert solve["status"] == "failed"
    assert residuals["status"] == "failed"
    assert feedback["strict_constraint_solve"]["status"] == "failed"
    assert feedback["strict_constraint_solve"].get("message")
    assert feedback["constraint_residuals"]["status"] == "failed"
    assert feedback["constraint_residuals"]["evidence"]["source"] == (
        "non_strict_diagnostic"
    )
    assert {
        residual["constraint_id"]
        for residual in feedback["constraint_residuals"]["evidence"]["residuals"]
    } == {"at_zero", "at_ten"}


def test_product_validation_feedback_is_bounded_and_prioritizes_failures() -> None:
    contacts = [
        {
            "position_mm": [float(index), 0.0, 0.0],
            "normal": [1.0, 0.0, 0.0],
            "penetration_depth_mm": 1.0,
        }
        for index in range(10)
    ]
    failures = [
        {
            "component_a": [f"first_{index}"],
            "component_b": [f"second_{index}"],
            "penetration_depth_mm": 1.0,
            "contacts": contacts,
        }
        for index in range(20)
    ]
    report = {
        "checks": [
            {"check_id": f"passed_{index}", "status": "passed"}
            for index in range(16)
        ]
        + [
            {
                "check_id": "current_pose_collision",
                "status": "failed",
                "evidence": {"failures": failures},
            }
        ]
    }

    checks = cad_executor._bounded_validation_checks(report)

    assert len(checks) == 16
    assert checks[0]["check_id"] == "current_pose_collision"
    assert checks[0]["evidence_truncated"] is True
    bounded_failures = checks[0]["evidence"]["failures"]
    assert len(bounded_failures) == 12
    assert len(bounded_failures[0]["contacts"]) == 4


def test_short_circuit_feedback_bounds_stdout_payload() -> None:
    contacts = [
        {"position_mm": [float(index), 0.0, 0.0]}
        for index in range(10)
    ]
    checks = cad_runner._short_circuit_validation_checks(
        [
            {"check_id": "strict_constraint_solve", "status": "passed"},
            {
                "check_id": "current_pose_collision",
                "status": "failed",
                "message": "x" * 3000,
                "evidence": {
                    "failures": [
                        {
                            "component_a": [f"first_{index}"],
                            "component_b": [f"second_{index}"],
                            "contacts": contacts,
                        }
                        for index in range(20)
                    ]
                },
            },
        ]
    )

    assert len(checks) == 1
    assert checks[0]["check_id"] == "current_pose_collision"
    assert len(checks[0]["message"]) == 2048
    assert checks[0]["evidence_truncated"] is True
    assert len(checks[0]["evidence"]["failures"]) == 12
    assert len(checks[0]["evidence"]["failures"][0]["contacts"]) == 4


def test_empty_assembly_is_reported_as_a_topology_failure(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    return cad.make_assembly_rassembly(assembly_id="empty")
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.exit_code == 0
    assert result.result_kind == "assembly"
    assert result.error_type == "topology"
    assert "must contain components" in (result.error or "")
    assert result.scene_artifact_exists is False


def test_cad_execution_counts_nested_assembly_structure(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    bearing = cad.make_part_rpart(
        part_id="bearing",
        body=cad.make_cylinder_rsolid(radius=2.0, height=1.0),
    )
    cartridge = cad.make_assembly_rassembly(assembly_id="cartridge")
    cartridge = cad.add_component_rassembly(
        assembly=cartridge,
        item=bearing,
        component_id="bearing",
        placement=cad.identity_placement_rplacement(),
    )
    housing = cad.make_part_rpart(
        part_id="housing",
        body=cad.make_box_rsolid(width=8.0, height=8.0, depth=2.0),
    )
    drive = cad.make_assembly_rassembly(assembly_id="drive")
    drive = cad.add_component_rassembly(
        assembly=drive,
        item=cartridge,
        component_id="input_cartridge",
        placement=cad.identity_placement_rplacement(),
    )
    return cad.add_component_rassembly(
        assembly=drive,
        item=housing,
        component_id="housing",
        placement=cad.make_placement_rplacement(origin=(0.0, 0.0, 3.0)),
    )
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "succeeded"
    assert result.result_kind == "assembly"
    assert result.component_count == 3
    assert result.leaf_part_count == result.solid_count == 2
    assert result.scene_parse_result.geometry_asset_count == 2


def test_nonzero_model_exit_is_observable(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "raise RuntimeError('model failed')\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=15.0)

    assert result.status == "failed"
    assert result.exit_code != 0
    assert "model failed" in (result.error or "")
    assert result.error_type == "execution"


def test_syntax_error_is_reported_by_preflight_without_starting_cad(
    tmp_path: Path,
) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "def build_model(model):\n    return (\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=15.0)

    assert result.status == "failed"
    assert result.preflight_status == "failed"
    assert result.error_type == "syntax"
    assert result.process_id is None
    assert result.error_location is not None
    assert "model.py" in result.error_location


def test_import_error_is_classified_after_source_preflight(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import package_that_does_not_exist\n\n"
        "def build_model(model):\n    raise AssertionError('unreachable')\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=15.0)

    assert result.status == "failed"
    assert result.preflight_status == "failed"
    assert result.error_type == "import"
    assert result.error_location is not None


def test_api_error_in_model_source_is_classified_with_location(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    return model.not_a_cadflow_method()
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.preflight_status == "passed"
    assert result.error_type == "api"
    assert result.error_location is not None
    assert "model.py" in result.error_location


def test_invalid_scene_artifact_is_not_promoted_to_success(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """from pathlib import Path
import cadflow as cad

def build_model(model: cad.Model):
    def invalid_export_scene(*, package, path):
        Path(path).write_bytes(b'not a Scene ZIP')
    cad.export_scene = invalid_export_scene
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.final_shape_count == 1
    assert result.scene_artifact_exists is True
    assert result.scene_parse_result.valid is False
    assert "zip" in (result.error or "").lower()


def test_final_shape_count_must_be_exactly_one(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = """import cadflow as cad

def build_model(model: cad.Model):
    return [model.box(width=10.0, depth=10.0, height=10.0)]
"""
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.final_shape_count is None
    assert "CadFlow Shape" in (result.error or "")


def test_multi_solid_shape_requires_an_assembly_result(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

def build_model(model: cad.Model):
    first = model.box(width=2.0, depth=2.0, height=2.0)
    second = model.translate(
        model.box(width=2.0, depth=2.0, height=2.0),
        x=10.0,
        y=0.0,
        z=0.0,
    )
    return model.union(first, second)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.result_kind == "part"
    assert result.solid_count == 2
    assert "must return cad.Assembly" in (result.error or "")
    assert result.scene_artifact_exists is False


def test_non_solid_return_is_observable_as_failure(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    source = """import cadflow as cad

def build_model(model: cad.Model):
    return model.polyline(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
"""
    scaffold.model_path.write_text(source, encoding="utf-8")

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert result.result_kind == "part"
    assert result.final_shape_count == 1
    assert "solid-compatible" in (result.error or "")


def test_unexpected_artifact_member_is_not_a_validated_result(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """from pathlib import Path
import cadflow as cad

PROJECT_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = PROJECT_DIR / "artifacts"

def build_model(model: cad.Model):
    (ARTIFACT_DIR / "unexpected.log").write_text("debug", encoding="utf-8")
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    assert result.status == "failed"
    assert "unexpected.log" in result.artifact_entries
    assert result.scene_parse_result.valid is True
    assert "not declared by product.json" in (result.error or "")


def test_timeout_stops_model_process(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import time\ntime.sleep(5)\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=0.1)

    assert result.status == "timed_out"
    assert result.exit_code is not None
    assert "timed out" in (result.error or "").lower()


def test_timeout_reports_the_active_cad_validation_phase(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        "import time\n"
        "import cadflow as cad\n\n"
        "def build_model(model: cad.Model):\n"
        "    time.sleep(10)\n"
        "    return model.box(width=1.0, depth=1.0, height=1.0)\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, timeout_seconds=4.0)

    assert result.status == "timed_out"
    assert result.execution_phase == "model_build"
    assert "during model_build" in (result.error or "")
    assert "simplify expensive booleans" in (result.error or "")


def test_external_cancellation_terminates_model_process(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import time\ntime.sleep(10)\n",
        encoding="utf-8",
    )
    token = CancellationToken()
    results: list[object] = []

    worker = threading.Thread(
        target=lambda: results.append(
            CADExecutor().execute(
                tmp_path,
                timeout_seconds=10.0,
                cancellation_token=token,
            )
        )
    )
    worker.start()
    time.sleep(0.2)
    token.cancel()
    worker.join(timeout=5.0)

    assert not worker.is_alive()
    assert len(results) == 1
    assert results[0].status == "cancelled"
    assert results[0].process_id is not None
    assert "cancel" in (results[0].error or "").lower()


def test_output_is_bounded_and_credential_like_text_is_redacted(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "model.py").write_text(
        "import sys\n"
        "sys.stdout.write('OPENAI_API_KEY=sk-test-secret-value\\n' + 'x' * 1000)\n"
        "sys.stdout.flush()\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )

    result = CADExecutor().execute(tmp_path, max_output_bytes=128, timeout_seconds=15.0)

    assert result.status == "failed"
    assert result.stdout_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 128
    assert "sk-test-secret-value" not in result.stdout
    assert "OPENAI_API_KEY=sk-test-secret-value" not in (result.error or "")
    assert "[REDACTED]" in result.stdout


def test_cad_environment_removes_provider_credentials_but_keeps_runtime_values() -> (
    None
):
    environment = build_cad_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_BASE_URL": "https://provider.invalid/v1",
            "MODEL_API_ENDPOINT": "https://provider.invalid/model",
            "LANGCHAIN_API_KEY": "lang-secret",
            "PATH": "/usr/bin",
            "SAFE_RUNTIME_VALUE": "kept",
        }
    )

    assert "OPENAI_API_KEY" not in environment
    assert "OPENAI_BASE_URL" not in environment
    assert "MODEL_API_ENDPOINT" not in environment
    assert "LANGCHAIN_API_KEY" not in environment
    assert environment["PATH"] == "/usr/bin"
    assert environment["SAFE_RUNTIME_VALUE"] == "kept"
