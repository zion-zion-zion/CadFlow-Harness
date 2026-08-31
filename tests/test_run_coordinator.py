from __future__ import annotations

import threading
import time
from pathlib import Path

from backend.agent import AgentRunOutcome
from backend.cad_executor import CancellationToken
from backend.projects import ProjectState, ProjectStateError, ProjectStore
from backend.run_coordinator import AgentRunCoordinator


class _NoopPreview:
    def activate(self, _project_id: str) -> None:
        return

    def deactivate(self, _project_id: str, *, validated: bool = False) -> None:
        del validated


class _FixedOutcomeService:
    def __init__(self, outcome: AgentRunOutcome) -> None:
        self.outcome = outcome

    def run(
        self,
        _project_id: str,
        _prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: object,
        conversation_log: object,
    ) -> AgentRunOutcome:
        del cancellation_token, progress_callback, conversation_log
        return self.outcome


class _LateSuccessService:
    def __init__(self) -> None:
        self.started = threading.Event()

    def run(
        self,
        _project_id: str,
        _prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: object,
        conversation_log: object,
    ) -> AgentRunOutcome:
        del progress_callback, conversation_log
        self.started.set()
        while not cancellation_token.cancelled:
            time.sleep(0.005)
        return AgentRunOutcome(validated=True)


def _coordinator(
    root: Path,
    service: object,
) -> tuple[ProjectStore, AgentRunCoordinator, str]:
    store = ProjectStore(root)
    project = store.create_project("Lifecycle")
    coordinator = AgentRunCoordinator(
        store=store,
        repo_root=root,
        run_service=service,
        preview_scheduler=_NoopPreview(),
    )
    return store, coordinator, project.project_id


def test_artifact_promotion_failure_is_one_failed_terminalization(
    tmp_path: Path,
) -> None:
    service = _FixedOutcomeService(AgentRunOutcome(validated=True))
    store, coordinator, project_id = _coordinator(tmp_path, service)

    def fail_promotion(
        _project_id: str,
        _diagnostics: object = None,
    ) -> object:
        raise ProjectStateError("artifact promotion failed")

    store.mark_succeeded = fail_promotion  # type: ignore[method-assign]
    submission = coordinator.start_message(
        project_id,
        "Create a part.",
        request_id="request-1",
    )
    assert coordinator.wait_for_turn(submission.turn_id, 2.0)

    project = store.get_project(project_id)
    assert project.state is ProjectState.FAILED
    assert project.failure_reason == "artifact promotion failed"
    turn = store.conversation_log(project_id).turn(submission.turn_id)
    assert turn is not None
    assert turn["status"] == "failed"
    records = store.conversation_log(project_id).data["records"]
    assert [record["type"] for record in records].count("turn_failed") == 1
    assert [record["type"] for record in records].count("artifact_committed") == 0
    events = coordinator.events.read_after(project_id, 0)
    assert [event.stage for event in events].count("failed") == 1
    assert [event.stage for event in events].count("completed") == 0


def test_stop_wins_over_a_late_successful_agent_outcome(tmp_path: Path) -> None:
    service = _LateSuccessService()
    store, coordinator, project_id = _coordinator(tmp_path, service)

    submission = coordinator.start_message(
        project_id,
        "Create a part and stop it.",
        request_id="request-1",
    )
    assert service.started.wait(1.0)
    stopped = coordinator.stop(project_id)

    assert stopped.state is ProjectState.STOPPED
    assert coordinator.wait_for_turn(submission.turn_id, 1.0)
    turn = store.conversation_log(project_id).turn(submission.turn_id)
    assert turn is not None
    assert turn["status"] == "stopped"
    records = store.conversation_log(project_id).data["records"]
    assert [record["type"] for record in records].count("turn_failed") == 1
    assert [record["type"] for record in records].count("artifact_committed") == 0
    events = coordinator.events.read_after(project_id, 0)
    assert [event.stage for event in events].count("stopped") == 1
    assert [event.stage for event in events].count("completed") == 0


def test_agent_configuration_failure_is_finalized_by_coordinator(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Configuration failure")

    def fail_settings() -> object:
        raise RuntimeError("configuration unavailable")

    coordinator = AgentRunCoordinator(
        store=store,
        repo_root=tmp_path,
        settings_factory=fail_settings,
        preview_scheduler=_NoopPreview(),
    )
    submission = coordinator.start_message(
        project.project_id,
        "Create a part.",
        request_id="request-1",
    )
    assert coordinator.wait_for_turn(submission.turn_id, 2.0)

    assert store.get_project(project.project_id).state is ProjectState.FAILED
    turn = store.conversation_log(project.project_id).turn(submission.turn_id)
    assert turn is not None
    assert turn["status"] == "failed"
    assert turn["error"] == "configuration unavailable"
