from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from backend.agent import AgentRunError, create_agent_tools
from backend.cad_executor import ExecutionResult
from backend.cad_review import ReviewFinding, ReviewResult
from backend.failure_packet import (
    FailureType,
    normalize_execution_failure,
    normalize_review_failure,
)
from backend.projects import ProjectStore
from backend.repair_state import (
    DesignContractError,
    ProjectRepairState,
    RunIdentity,
)
from backend.restricted_tools import AgentModelValidator
from backend.scene_validation import SceneParseResult


def _identity(project_id: str, *, turn_id: str = "turn-1") -> RunIdentity:
    return RunIdentity(
        project_id=project_id,
        turn_id=turn_id,
        request_id=f"request-{turn_id}",
        request_text="Create a 40 x 30 x 8 mm plate with two 5 mm holes.",
    )


def _contract_fields() -> dict[str, object]:
    return {
        "task_type": "single_part",
        "explicit_requirements": [
            "Plate is 40 x 30 x 8 mm.",
            "Two through holes have 5 mm diameter.",
        ],
        "key_components": ["base plate", "through holes"],
        "assumptions": ["Hole centers are symmetric about the plate center."],
        "implementation_stages": ["Create plate", "Cut both holes"],
    }


def test_design_contract_is_validated_and_bound_to_the_current_request(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Contract binding")
    state = ProjectRepairState(store.project_directory(project.project_id))
    identity = _identity(project.project_id)

    with pytest.raises(DesignContractError, match="explicit_requirements"):
        state.submit_design_contract(
            identity,
            **{**_contract_fields(), "explicit_requirements": []},
        )

    contract = state.submit_design_contract(identity, **_contract_fields())

    assert contract.project_id == project.project_id
    assert contract.turn_id == "turn-1"
    assert contract.request_id == "request-turn-1"
    assert contract.request_text == identity.request_text
    assert state.design_contract(identity) == contract
    assert state.design_contract(_identity(project.project_id, turn_id="turn-2")) is None
    changed_request = RunIdentity(
        project_id=project.project_id,
        turn_id="turn-1",
        request_id="request-turn-1",
        request_text="Create a different part.",
    )
    assert state.design_contract(changed_request) is None

    store.submit_prompt(project.project_id, identity.request_text)
    store.mark_failed(project.project_id, "CAD validation failed")
    reloaded = ProjectRepairState(store.project_directory(project.project_id))
    assert reloaded.design_contract(identity) == contract


def test_legacy_project_without_repair_state_loads_as_empty(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Legacy")
    state = ProjectRepairState(store.project_directory(project.project_id))

    assert state.design_contract(_identity(project.project_id)) is None
    assert state.attempts() == ()
    assert state.last_passing_source() is None


def test_first_validation_requires_a_contract_for_the_current_request(
    tmp_path: Path,
) -> None:
    class CountingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, *_args: object, **_kwargs: object) -> ExecutionResult:
            self.calls += 1
            return ExecutionResult(
                status="failed",
                exit_code=1,
                error="geometry failed",
                stdout="",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                final_shape_count=None,
                solid_count=None,
                solid_volume=None,
                scene_artifact_exists=False,
                scene_parse_result=SceneParseResult(valid=False),
                artifact_entries=(),
                duration_seconds=0.1,
                error_type="geometry",
            )

    project_dir = tmp_path / "project-1"
    identity = _identity("project-1")
    state = ProjectRepairState(project_dir)
    executor = CountingExecutor()
    validator = AgentModelValidator(project_dir=project_dir, executor=executor)
    validator.begin_run()
    tools = {
        item.name: item
        for item in create_agent_tools(
            validator,
            request_text=identity.request_text,
            repair_state=state,
            run_identity=identity,
        )
    }

    assert set(tools) == {
        "submit_design_contract",
        "validate_model",
        "cad_review",
    }
    with pytest.raises(AgentRunError, match="Design Contract"):
        tools["validate_model"].invoke({})
    assert executor.calls == 0

    submitted = tools["submit_design_contract"].invoke(_contract_fields())
    result = tools["validate_model"].invoke({})

    assert submitted["status"] == "accepted"
    assert submitted["contract"]["turn_id"] == identity.turn_id
    assert result["status"] == "failed"
    assert result["error"] == "geometry failed"
    assert result["failure_packet"]["primary_type"] == "GEOMETRY_ERROR"
    assert result["attempt_feedback"]["repeated_failure"] is False
    assert len(state.attempts(identity)) == 1
    assert executor.calls == 1


def _failed_execution(
    *,
    error_type: str,
    result_kind: str | None = None,
    checks: tuple[dict[str, object], ...] = (),
    stdout: str = "raw executor output",
    duration: float = 0.1,
) -> ExecutionResult:
    return ExecutionResult(
        status="failed",
        exit_code=1,
        error="Model validation failed at /private/tmp/run/code/model.py:42",
        stdout=stdout,
        stderr="full traceback retained elsewhere",
        stdout_truncated=False,
        stderr_truncated=False,
        final_shape_count=1 if result_kind else None,
        solid_count=2 if result_kind == "assembly" else None,
        solid_volume=120.0 if result_kind else None,
        scene_artifact_exists=False,
        scene_parse_result=SceneParseResult(valid=False),
        artifact_entries=(),
        duration_seconds=duration,
        error_type=error_type,
        error_location="code/model.py:42",
        result_kind=result_kind,
        product_validation_status="Draft" if checks else None,
        product_validation_failures=tuple(
            str(check["message"])
            for check in checks
            if check.get("status") == "failed"
        ),
        product_validation_checks=checks,
    )


@pytest.mark.parametrize(
    ("result", "expected_type"),
    [
        (_failed_execution(error_type="syntax"), FailureType.EXECUTION_ERROR),
        (_failed_execution(error_type="topology"), FailureType.GEOMETRY_ERROR),
        (
            _failed_execution(error_type="infrastructure"),
            FailureType.INFRASTRUCTURE_ERROR,
        ),
        (
            _failed_execution(
                error_type="product_validation",
                result_kind="assembly",
                checks=(
                    {"check_id": "leaf_geometry", "status": "passed"},
                    {
                        "check_id": "strict_constraint_solve",
                        "status": "failed",
                        "message": "Constraint solve residual exceeded tolerance",
                    },
                ),
            ),
            FailureType.ASSEMBLY_ERROR,
        ),
        (
            _failed_execution(error_type="review_evidence"),
            FailureType.INFRASTRUCTURE_ERROR,
        ),
    ],
)
def test_execution_failures_are_normalized_to_typed_packets(
    result: ExecutionResult,
    expected_type: FailureType,
) -> None:
    packet = normalize_execution_failure(result)

    assert packet is not None
    assert packet.primary_type is expected_type
    assert packet.failure_signature.startswith(f"{expected_type.value}:")
    assert packet.key_evidence
    if expected_type is FailureType.ASSEMBLY_ERROR:
        assert "leaf_geometry" in packet.preserve_conditions
    if expected_type is FailureType.INFRASTRUCTURE_ERROR:
        assert packet.source_edit_allowed is False
        assert packet.source_scope == ()
        assert "Do not modify CAD source" in packet.suggested_action


def test_review_failure_is_normalized_as_requirement_review_error() -> None:
    packet = normalize_review_failure(
        ReviewResult(
            status="fail",
            summary="The requested second hole is missing.",
            findings=(
                ReviewFinding(
                    category="requirement",
                    severity="blocking",
                    requirement="Two through holes are required.",
                    observed="Only one hole is visible.",
                ),
            ),
            checked_requirements=("plate dimensions", "two through holes"),
        )
    )

    assert packet is not None
    assert packet.primary_type is FailureType.REQUIREMENT_REVIEW_ERROR
    assert packet.source_edit_allowed is True
    assert packet.source_scope == ("code/model.py",)


def test_failure_signature_ignores_volatile_raw_diagnostics() -> None:
    first = normalize_execution_failure(
        _failed_execution(error_type="syntax", stdout="first run pid=123", duration=0.1)
    )
    second = normalize_execution_failure(
        _failed_execution(error_type="syntax", stdout="second run pid=999", duration=9.7)
    )

    assert first is not None and second is not None
    assert first.failure_signature == second.failure_signature

    first_constraint = normalize_execution_failure(
        _failed_execution(
            error_type="product_validation",
            result_kind="assembly",
            checks=(
                {
                    "check_id": "strict_constraint_solve",
                    "status": "failed",
                    "message": "Residual 1.234 mm at constraint 17",
                },
            ),
        )
    )
    second_constraint = normalize_execution_failure(
        _failed_execution(
            error_type="product_validation",
            result_kind="assembly",
            checks=(
                {
                    "check_id": "strict_constraint_solve",
                    "status": "failed",
                    "message": "Residual 1.287 mm at constraint 17",
                },
            ),
        )
    )
    assert first_constraint is not None and second_constraint is not None
    assert first_constraint.failure_signature == second_constraint.failure_signature


def _passing_execution() -> ExecutionResult:
    return ExecutionResult(
        status="succeeded",
        exit_code=0,
        error=None,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        final_shape_count=1,
        solid_count=1,
        solid_volume=9600.0,
        scene_artifact_exists=True,
        scene_parse_result=SceneParseResult(valid=True),
        artifact_entries=("model.scene.zip", "product.json", "validation.json"),
        duration_seconds=0.2,
        result_kind="part",
        component_count=0,
        leaf_part_count=1,
        unique_part_count=1,
        product_manifest_path="artifacts/product.json",
        product_status="Draft",
        product_validation_status="Passed",
        product_validation_checks=(
            {"check_id": "leaf_geometry", "status": "passed"},
            {"check_id": "step_export_replay", "status": "passed"},
        ),
    )


def test_attempt_ledger_records_validation_review_and_last_passing_source(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project-1"
    code_dir = project_dir / "code"
    code_dir.mkdir(parents=True)
    (code_dir / "model.py").write_text("REVISION = 1\n", encoding="utf-8")
    identity = _identity("project-1")
    state = ProjectRepairState(project_dir)
    contract = state.submit_design_contract(identity, **_contract_fields())

    first = state.record_validation(
        identity,
        _failed_execution(error_type="syntax"),
    )
    (code_dir / "model.py").write_text("REVISION = 2\n", encoding="utf-8")
    (code_dir / "holes.py").write_text("COUNT = 2\n", encoding="utf-8")
    passing = state.record_validation(identity, _passing_execution())
    reviewed = state.record_review(
        identity,
        ReviewResult(
            status="fail",
            summary="One requested hole is missing.",
            findings=(
                ReviewFinding(
                    category="missing_feature",
                    severity="blocking",
                    requirement="Two holes",
                    observed="One hole",
                ),
            ),
        ),
    )

    attempts = state.attempts()
    assert attempts == (first, passing, reviewed)
    assert attempts[0].request_text == identity.request_text
    assert attempts[0].design_contract == contract
    assert attempts[1].changed_files == ("code/holes.py", "code/model.py")
    assert attempts[1].validation_status == "passed"
    assert attempts[2].attempt_kind == "review"
    assert attempts[2].review_status == "fail"
    assert attempts[2].failure_type is FailureType.REQUIREMENT_REVIEW_ERROR

    snapshot = state.last_passing_source()
    assert snapshot is not None
    assert snapshot.source_revision == passing.source_revision
    assert snapshot.archive_path.is_file()
    with zipfile.ZipFile(snapshot.archive_path) as archive:
        assert set(archive.namelist()) == {"code/holes.py", "code/model.py"}
        assert archive.read("code/model.py") == b"REVISION = 2\n"


def test_attempt_ledger_detects_repeated_failure_oscillation_and_broken_check(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project-1"
    code_dir = project_dir / "code"
    code_dir.mkdir(parents=True)
    model_path = code_dir / "model.py"
    identity = _identity("project-1")
    state = ProjectRepairState(project_dir)
    state.submit_design_contract(identity, **_contract_fields())
    assembly_failure = _failed_execution(
        error_type="product_validation",
        result_kind="assembly",
        checks=(
            {"check_id": "leaf_geometry", "status": "passed"},
            {
                "check_id": "strict_constraint_solve",
                "status": "failed",
                "message": "Constraint solve residual exceeded tolerance",
            },
        ),
    )

    model_path.write_text("REVISION = 1\n", encoding="utf-8")
    state.record_validation(identity, assembly_failure)
    model_path.write_text("REVISION = 2\n", encoding="utf-8")
    repeated = state.record_validation(identity, assembly_failure)
    model_path.write_text("REVISION = 3\n", encoding="utf-8")
    state.record_validation(identity, _failed_execution(error_type="topology"))
    model_path.write_text("REVISION = 4\n", encoding="utf-8")
    oscillating = state.record_validation(identity, assembly_failure)
    model_path.write_text("REVISION = 5\n", encoding="utf-8")
    broken = state.record_validation(
        identity,
        _failed_execution(
            error_type="product_validation",
            result_kind="assembly",
            checks=(
                {
                    "check_id": "leaf_geometry",
                    "status": "failed",
                    "message": "A leaf has invalid volume",
                },
            ),
        ),
    )

    assert repeated.signals.repeated_failure is True
    assert any("repeated" in hint.lower() for hint in repeated.signals.hints)
    assert oscillating.signals.oscillation is True
    assert "leaf_geometry" in broken.signals.broken_conditions


def test_failed_change_after_a_pass_is_flagged_as_last_passing_regression(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project-1"
    code_dir = project_dir / "code"
    code_dir.mkdir(parents=True)
    model_path = code_dir / "model.py"
    identity = _identity("project-1")
    state = ProjectRepairState(project_dir)
    state.submit_design_contract(identity, **_contract_fields())

    model_path.write_text("REVISION = 'passing'\n", encoding="utf-8")
    passed = state.record_validation(identity, _passing_execution())
    model_path.write_text("REVISION = 'regressed'\n", encoding="utf-8")
    failed = state.record_validation(
        identity,
        _failed_execution(error_type="topology"),
    )

    assert failed.signals.regression_from_last_passing is True
    assert failed.signals.last_passing_revision == passed.source_revision
    assert failed.signals.changed_from_last_passing == ("code/model.py",)
    assert state.last_passing_source() is not None
    assert state.last_passing_source().source_revision == passed.source_revision
