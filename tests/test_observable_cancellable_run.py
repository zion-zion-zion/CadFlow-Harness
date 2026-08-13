from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent import AgentRunOutcome
from backend.cad_executor import CancellationToken
from backend.events import ProgressEventStore
from backend.app import create_app
from backend.events import ProgressUpdate
from backend.model_source import create_model_source
from backend.projects import ProjectState, ProjectStateError, ProjectStore


def test_progress_events_are_persisted_with_monotonic_ids_and_replayed(
    tmp_path: Path,
) -> None:
    events = ProgressEventStore(tmp_path)

    first = events.append(
        "a" * 32,
        stage="preparing",
        tool="project",
        result="Project prepared",
    )
    second = events.append(
        "a" * 32,
        stage="executing",
        tool="cad",
        attempt=1,
        result="CAD attempt failed: bounded error",
    )

    assert first.event_id == 1
    assert second.event_id == 2
    assert events.read_after("a" * 32, 1) == (second,)

    reloaded = ProgressEventStore(tmp_path)
    assert reloaded.read_after("a" * 32, 0) == (first, second)
    lines = (tmp_path / ("a" * 32) / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == [1, 2]


def test_progress_event_sse_payload_is_curated_and_has_no_raw_diagnostics(
    tmp_path: Path,
) -> None:
    events = ProgressEventStore(tmp_path)

    event = events.append(
        "b" * 32,
        stage="failed",
        tool="agent",
        result="Traceback: OPENAI_API_KEY=sk-secret-value\nfull stderr hidden",
    )

    payload = event.to_sse()

    assert payload.startswith("id: 1\nevent: progress\ndata: ")
    assert "sk-secret-value" not in payload
    assert "full stderr hidden" not in payload
    assert '"stdout"' not in payload
    assert '"stderr"' not in payload


def test_stopped_project_keeps_source_prompt_events_and_diagnostics_but_not_scene(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Stopped flange")
    running = store.submit_prompt(project.project_id, "Create a flange.")
    scaffold = create_model_source(tmp_path / project.project_id)
    artifact = scaffold.scene_path
    artifact.write_bytes(b"partial scene")

    stopped = store.mark_stopped(
        project.project_id,
        "Agent Run stopped by user",
        {"status": "stopped", "cad_execution_count": 1},
    )

    assert running.prompt == stopped.prompt == "Create a flange."
    assert stopped.state is ProjectState.STOPPED
    assert scaffold.model_path.is_file()
    assert store.read_diagnostics(project.project_id) == {
        "status": "stopped",
        "cad_execution_count": 1,
    }
    assert not artifact.exists()
    with pytest.raises(ProjectStateError):
        store.submit_prompt(project.project_id, "Run again.")


def test_restart_rebuild_marks_legacy_running_project_failed(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Interrupted")
    store.submit_prompt(project.project_id, "Create a box.")

    reloaded = ProjectStore(tmp_path)
    recovered = reloaded.recover_interrupted_runs()

    assert [item.project_id for item in recovered] == [project.project_id]
    failed = reloaded.get_project(project.project_id)
    assert failed.state is ProjectState.FAILED
    assert failed.failure_reason == "Agent Run was interrupted by service restart"
    diagnostics = reloaded.read_diagnostics(project.project_id)
    assert diagnostics is not None
    assert diagnostics["recovered_after_restart"] is True


class _LifecycleRunHarness:
    def __init__(self) -> None:
        self.started = threading.Event()

    def run(
        self,
        _project_id: str,
        _prompt: str,
        *,
        cancellation_token: object,
        progress_callback: object,
        prompt_submitted: bool,
    ) -> AgentRunOutcome:
        assert prompt_submitted is True
        assert callable(progress_callback)
        progress_callback(ProgressUpdate(stage="reading_references", tool="reference"))  # type: ignore[operator]
        self.started.set()
        while not getattr(cancellation_token, "cancelled"):
            time.sleep(0.005)
        return AgentRunOutcome(
            validated=False,
            cancelled=True,
            failure_reason="Agent Run stopped by caller",
        )


class _TimeoutRunHarness:
    def run(
        self,
        _project_id: str,
        _prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: object,
        prompt_submitted: bool,
    ) -> AgentRunOutcome:
        assert prompt_submitted is True
        cancellation_token.cancel(reason="timeout")
        return AgentRunOutcome(
            validated=False,
            failure_reason="Agent Run exceeded the ten-minute wall-clock limit",
            duration_seconds=600.0,
        )


def test_internal_timeout_is_failed_instead_of_stopped(tmp_path: Path) -> None:
    app = create_app(projects_root=tmp_path, run_service=_TimeoutRunHarness())
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Timeout"}).json()

    started = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": "Create a box."},
    )
    assert started.status_code == 202
    assert app.state.run_coordinator.wait_for_idle(1.0)

    failed = client.get(f"/api/projects/{project['project_id']}").json()
    assert failed["state"] == "Failed"
    assert failed["failure_reason"] == (
        "Agent Run exceeded the ten-minute wall-clock limit"
    )
    events = app.state.event_store.read_after(project["project_id"], 0)
    assert events[-1].stage == "failed"


def test_http_global_run_conflict_and_stop_boundary_for_lifecycle_harness(
    tmp_path: Path,
) -> None:
    service = _LifecycleRunHarness()
    app = create_app(projects_root=tmp_path, run_service=service)
    client = TestClient(app)
    first = client.post("/api/projects", json={"name": "First"}).json()
    second = client.post("/api/projects", json={"name": "Second"}).json()

    started = client.post(
        f"/api/projects/{first['project_id']}/run",
        json={"prompt": "Create the first part."},
    )
    assert started.status_code == 202
    assert service.started.wait(1.0)

    conflict = client.post(
        f"/api/projects/{second['project_id']}/run",
        json={"prompt": "Create the second part."},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "another Agent Run is already active"

    stopped = client.post(f"/api/projects/{first['project_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "Stopped"
    assert app.state.run_coordinator.active_project_id is None
    assert (
        app.state.project_store.get_project(first["project_id"]).state
        is ProjectState.STOPPED
    )


def test_sse_replays_after_last_event_id_and_keeps_payload_curated(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path, run_service=_LifecycleRunHarness())
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Events"}).json()
    event_store: ProgressEventStore = app.state.event_store
    event_store.append(
        project["project_id"], stage="preparing", tool="service", result="one"
    )
    event_store.append(
        project["project_id"], stage="writing_model", tool="model_source", result="two"
    )

    with client.stream(
        "GET",
        f"/api/projects/{project['project_id']}/events?follow=false",
        headers={"Last-Event-ID": "1"},
    ) as response:
        assert response.status_code == 200
        lines = []
        for line in response.iter_lines():
            lines.append(line)
            if line == "":
                break

    body = "\n".join(lines)
    assert "id: 2" in body
    assert "writing_model" in body
    assert "stdout" not in body
    assert "stderr" not in body


def test_app_restart_recovers_running_project_without_an_active_lock(
    tmp_path: Path,
) -> None:
    first = create_app(projects_root=tmp_path, run_service=_LifecycleRunHarness())
    client = TestClient(first)
    project = client.post("/api/projects", json={"name": "Restart"}).json()
    first.state.project_store.submit_prompt(project["project_id"], "Interrupted prompt")

    restarted = create_app(projects_root=tmp_path, run_service=_LifecycleRunHarness())

    recovered = restarted.state.project_store.get_project(project["project_id"])
    assert recovered.state is ProjectState.FAILED
    assert recovered.failure_reason == "Agent Run was interrupted by service restart"
    assert restarted.state.run_coordinator.active_project_id is None
