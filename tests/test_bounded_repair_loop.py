from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent import (
    AgentRunError,
    AgentRunOutcome,
    AgentRunService,
    AgentSettings,
    ReasoningEffort,
    build_chat_model,
    create_agent_tools,
)
from backend.cad_executor import ExecutionResult
from backend.projects import ProjectState, ProjectStateError, ProjectStore
from backend.restricted_tools import RestrictedAgentTools
from backend.scene_validation import SceneParseResult


def _execution_result(
    *,
    status: str = "failed",
    error: str | None = "model source failed",
    volume: float | None = None,
    scene_valid: bool = False,
) -> ExecutionResult:
    return ExecutionResult(
        status=status,
        exit_code=0 if status == "succeeded" else 1,
        error=error,
        stdout="bounded stdout",
        stderr="bounded stderr",
        stdout_truncated=False,
        stderr_truncated=False,
        final_shape_count=1 if volume is not None else 0,
        solid_count=1 if volume is not None else 0,
        solid_volume=volume,
        scene_artifact_exists=scene_valid,
        scene_parse_result=SceneParseResult(valid=scene_valid),
        artifact_entries=("model.scene.zip",) if scene_valid else (),
        duration_seconds=0.25,
    )


def _validation_tools(tmp_path: Path, executor: object, **kwargs: object):
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
        executor=executor,
    )
    restricted.begin_run()
    tools = create_agent_tools(restricted, **kwargs)
    by_name = {item.name: item for item in tools}
    return restricted, by_name


def test_validate_model_allows_retries_beyond_three_attempts(
    tmp_path: Path,
) -> None:
    class SequenceExecutor:
        def __init__(self) -> None:
            self.results = [
                _execution_result(error="first failure"),
                _execution_result(error="second failure"),
                _execution_result(error="third failure"),
                _execution_result(error="fourth failure"),
                _execution_result(
                    status="succeeded",
                    error=None,
                    volume=12.5,
                    scene_valid=True,
                ),
            ]
            self.calls = 0

        def execute(self, project_dir: Path, **kwargs: object) -> ExecutionResult:
            assert project_dir == tmp_path
            assert kwargs["timeout_seconds"] <= 120.0
            result = self.results[self.calls]
            self.calls += 1
            return result

    executor = SequenceExecutor()
    _restricted, tools = _validation_tools(
        tmp_path,
        executor,
        run_deadline=600.0,
        clock=lambda: 0.0,
    )

    results = [tools["validate_model"].invoke({}) for _ in range(5)]

    assert executor.calls == 5
    assert [item["error"] for item in results] == [
        "first failure",
        "second failure",
        "third failure",
        "fourth failure",
        None,
    ]
    assert results[-1]["final_shape_count"] == 1
    assert results[-1]["scene_parse_result"]["valid"] is True


def test_validate_model_does_not_request_preview_frames(
    tmp_path: Path,
) -> None:
    class RecordingExecutor:
        def execute(self, _project_dir: Path, **kwargs: object) -> ExecutionResult:
            assert "preview_callback" not in kwargs
            return _execution_result(
                status="succeeded",
                error=None,
                volume=12.5,
                scene_valid=True,
            )

    _restricted, tools = _validation_tools(
        tmp_path,
        RecordingExecutor(),
    )

    result = tools["validate_model"].invoke({})

    assert result["status"] == "succeeded"


def test_validate_model_refuses_to_start_after_run_deadline(tmp_path: Path) -> None:
    class NeverCalledExecutor:
        def execute(self, *_args: object, **_kwargs: object) -> ExecutionResult:
            raise AssertionError("CAD must not start after the Agent Run deadline")

    now = [0.0]
    _restricted, tools = _validation_tools(
        tmp_path,
        NeverCalledExecutor(),
        run_deadline=10.0,
        clock=lambda: now[0],
    )
    now[0] = 10.001

    with pytest.raises(AgentRunError, match="ten-minute wall-clock limit"):
        tools["validate_model"].invoke({})


def test_reference_agent_adapter_turns_boundary_exception_into_diagnosis(
    tmp_path: Path,
) -> None:
    class RaisingExecutor:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("CAD runner failed\nTraceback: hidden")

    records: list[ExecutionResult] = []
    _restricted, tools = _validation_tools(
        tmp_path,
        RaisingExecutor(),
        on_execution=records.append,
        on_execution_error=records.append,
        run_deadline=600.0,
        clock=lambda: 0.0,
    )

    result = tools["validate_model"].invoke({})

    assert result["status"] == "failed"
    assert result["error"] == "CAD runner failed"
    assert len(records) == 1
    assert records[0].stderr == ""


def test_failed_project_retains_all_attempt_diagnostics_but_never_exposes_scene(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Repair diagnostics")
    store.submit_prompt(project.project_id, "Make a part.")
    artifact = tmp_path / project.project_id / "artifacts" / "model.scene.zip"
    artifact.write_bytes(b"partial scene")

    diagnostics = {
        "cad_execution_count": 3,
        "execution_results": [
            {"attempt": 1, "error": "first failure", "stdout": "bounded"},
            {"attempt": 2, "error": "second failure", "stdout": "bounded"},
            {"attempt": 3, "error": "final failure", "stdout": "bounded"},
        ],
    }
    failed = store.mark_failed(project.project_id, "final failure", diagnostics)

    assert failed.state is ProjectState.FAILED
    assert store.read_diagnostics(project.project_id) == diagnostics
    with pytest.raises(ProjectStateError):
        store.scene_artifact(project.project_id)
    assert not artifact.exists()


def test_agent_run_service_persists_three_attempts_and_removes_partial_output(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Three attempts")

    class ReturningAgent:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def run(self, _prompt: str) -> AgentRunOutcome:
            results = tuple(
                _execution_result(error=f"failure {i}") for i in range(1, 4)
            )
            return AgentRunOutcome(
                validated=False,
                failure_reason="failure 3",
                execution_results=results,
            )

    service = AgentRunService(
        store=store,
        repo_root=Path(__file__).parents[1],
        settings_factory=lambda: AgentSettings(
            model_id="cad-model", api_key="test-key"
        ),
        agent_factory=ReturningAgent,
    )
    outcome = service.run(project.project_id, "Make a part.")

    assert outcome.validated is False
    assert store.get_project(project.project_id).state is ProjectState.FAILED
    diagnostics = store.read_diagnostics(project.project_id)
    assert diagnostics is not None
    assert diagnostics["cad_execution_count"] == 3
    assert [item["attempt"] for item in diagnostics["execution_results"]] == [1, 2, 3]
    assert not (tmp_path / project.project_id / "artifacts").exists() or not any(
        (tmp_path / project.project_id / "artifacts").iterdir()
    )
    agent_log = tmp_path / project.project_id / "conversation.jsonl"
    assert agent_log.is_file()
    records = [
        json.loads(line)
        for line in agent_log.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["payload"]["status"] == "failed"


@pytest.mark.parametrize(
    ("reasoning_effort", "use_responses_api"),
    [
        (None, True),
        ("none", False),
        ("low", True),
        ("medium", True),
        ("high", True),
        ("max", True),
    ],
)
def test_chat_model_selects_api_for_reasoning_effort(
    reasoning_effort: ReasoningEffort | None,
    use_responses_api: bool,
) -> None:
    model = build_chat_model(
        AgentSettings(
            model_id="cad-model",
            api_key="test-key",
            base_url="https://provider.invalid/v1",
            reasoning_effort=reasoning_effort,
        )
    )

    assert model.max_retries == 2
    assert model.use_responses_api is use_responses_api
    if use_responses_api:
        assert model.reasoning == (
            {"effort": reasoning_effort} if reasoning_effort is not None else None
        )
        assert model.reasoning_effort is None
    else:
        assert model.reasoning is None
        assert model.reasoning_effort == reasoning_effort


def test_chat_model_sends_reasoning_summary_only_to_responses() -> None:
    responses_model = build_chat_model(
        AgentSettings(
            model_id="cad-model",
            api_key="test-key",
            reasoning_effort="high",
            reasoning_summary="auto",
        )
    )
    chat_model = build_chat_model(
        AgentSettings(
            model_id="cad-model",
            api_key="test-key",
            reasoning_effort="none",
            reasoning_summary="auto",
        )
    )

    assert responses_model.reasoning == {"effort": "high", "summary": "auto"}
    assert chat_model.reasoning is None
    assert chat_model.reasoning_effort == "none"
