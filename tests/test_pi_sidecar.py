from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Mapping

from backend.agent import AgentSettings
from backend.cad_executor import ExecutionResult
from backend.pi_sidecar import PiAgentRunService
from backend.projects import ProjectStore
from backend.scene_validation import SceneParseResult


class _ScriptedSupervisor:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.available = True
        self.unavailable_reason: str | None = None
        self.frames = deque(frames)
        self.started_payload: dict[str, Any] | None = None
        self.validator_results: list[tuple[str, str, Mapping[str, Any]]] = []
        self.finished: list[str] = []
        self.restarted: list[str | None] = []

    def begin_run(self, run_id: str, payload: Mapping[str, Any]) -> None:
        self.started_payload = dict(payload)

    def next_frame(self, _run_id: str, _timeout_seconds: float) -> dict[str, Any] | None:
        return self.frames.popleft() if self.frames else None

    def send_validator_result(
        self,
        run_id: str,
        correlation_id: str,
        result: Mapping[str, Any],
    ) -> None:
        self.validator_results.append((run_id, correlation_id, result))
        self.frames.append(
            {
                "type": "complete",
                "run_id": run_id,
                "correlation_id": None,
                "payload": {
                    "token_usage": {
                        "input_tokens": 12,
                        "cached_input_tokens": 2,
                        "output_tokens": 7,
                    }
                },
            }
        )

    def abort(self, _run_id: str) -> None:
        return None

    def finish_run(self, run_id: str) -> None:
        self.finished.append(run_id)

    def force_restart(self, run_id: str | None = None) -> None:
        self.restarted.append(run_id)


class _ValidExecutor:
    def execute(self, _project_dir: Path, **_kwargs: object) -> ExecutionResult:
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
            solid_volume=1.0,
            scene_artifact_exists=True,
            scene_parse_result=SceneParseResult(valid=True),
            artifact_entries=("model.scene.zip",),
            duration_seconds=0.1,
        )


def _service(tmp_path: Path, supervisor: _ScriptedSupervisor) -> PiAgentRunService:
    return PiAgentRunService(
        store=ProjectStore(tmp_path),
        repo_root=Path(__file__).parents[1],
        supervisor=supervisor,  # type: ignore[arg-type]
        executor=_ValidExecutor(),
        settings_factory=lambda: AgentSettings(model_id="test-model", api_key="secret-key"),
    )


def test_pi_run_bridges_authoritative_validator_and_records_usage(tmp_path: Path) -> None:
    supervisor = _ScriptedSupervisor(
        [
            {"type": "run_started", "run_id": "", "correlation_id": None, "payload": {}},
            {"type": "validator_request", "run_id": "", "correlation_id": "validation-1", "payload": {}},
        ]
    )
    service = _service(tmp_path, supervisor)
    project = service.store.create_project("Pi")
    for frame in supervisor.frames:
        frame["run_id"] = project.project_id

    outcome = service.run(project.project_id, "Create a box.")

    assert outcome.validated is True
    assert outcome.harness.value == "pi"
    assert outcome.token_usage == {
        "total_tokens": 19,
        "input_tokens": 12,
        "cached_input_tokens": 2,
        "uncached_input_tokens": 10,
        "output_tokens": 7,
    }
    assert supervisor.validator_results[0][1] == "validation-1"
    assert supervisor.started_payload is not None
    assert supervisor.started_payload["provider"] == {
        "api_key": "secret-key",
        "model_id": "test-model",
        "base_url": None,
    }
    assert outcome.diagnostics()["harness"] == "pi"


def test_pi_completion_without_validation_cannot_succeed(tmp_path: Path) -> None:
    supervisor = _ScriptedSupervisor(
        [{"type": "complete", "run_id": "", "correlation_id": None, "payload": {}}]
    )
    service = _service(tmp_path, supervisor)
    project = service.store.create_project("No validation")
    supervisor.frames[0]["run_id"] = project.project_id

    outcome = service.run(project.project_id, "Create a box.")

    assert outcome.validated is False
    assert outcome.failure_reason == "Pi Agent finished without executing the Model Source"


def test_pi_worker_exit_fails_the_run_without_deepagents_fallback(tmp_path: Path) -> None:
    supervisor = _ScriptedSupervisor(
        [{"type": "process_exit", "run_id": "", "correlation_id": None, "payload": {"reason": "worker exited"}}]
    )
    service = _service(tmp_path, supervisor)
    project = service.store.create_project("Crash")
    supervisor.frames[0]["run_id"] = project.project_id

    outcome = service.run(project.project_id, "Create a box.")

    assert outcome.validated is False
    assert outcome.failure_reason == "worker exited"
    assert outcome.harness.value == "pi"
    assert supervisor.restarted == [project.project_id]


def test_pi_worker_failure_redacts_the_configured_api_key(tmp_path: Path) -> None:
    supervisor = _ScriptedSupervisor(
        [
            {
                "type": "failed",
                "run_id": "",
                "correlation_id": None,
                "payload": {"reason": "provider rejected secret-key"},
            }
        ]
    )
    service = _service(tmp_path, supervisor)
    project = service.store.create_project("Redacted failure")
    supervisor.frames[0]["run_id"] = project.project_id

    outcome = service.run(project.project_id, "Create a box.")

    assert outcome.failure_reason == "provider rejected [REDACTED]"
    assert "secret-key" not in str(outcome.diagnostics())
