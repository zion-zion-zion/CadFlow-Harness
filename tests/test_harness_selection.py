from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from backend.agent import DEEPAGENTS_IMPLEMENTATION_VERSION, AgentRunOutcome
from backend.agent_logging import ConversationLog
from backend.app import create_app
from backend.cad_executor import CancellationToken
from backend.events import ProgressUpdate
from backend.harnesses import AgentHarness


class _RunServiceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(
        self,
        project_id: str,
        prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: Callable[[ProgressUpdate], None],
        conversation_log: ConversationLog,
    ) -> AgentRunOutcome:
        del cancellation_token, progress_callback, conversation_log
        self.calls.append((project_id, prompt))
        return AgentRunOutcome(
            validated=False,
            failure_reason="deterministic run service failure",
        )


def _wait_for_terminal(client: TestClient, project_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        project = client.get(f"/api/projects/{project_id}").json()
        if project["state"] in {"Failed", "Succeeded", "Stopped"}:
            return project
        time.sleep(0.01)
    raise AssertionError("project did not reach a terminal state")


def test_run_api_defaults_to_deepagents_and_persists_harness(tmp_path: Path) -> None:
    service = _RunServiceStub()
    app = create_app(projects_root=tmp_path, run_service=service)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Default"}).json()

    started = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": "Create a test part."},
    )
    assert started.status_code == 202
    assert started.json()["harness"] == AgentHarness.DEEPAGENTS.value
    finished = _wait_for_terminal(client, project["project_id"])
    assert finished["harness"] == AgentHarness.DEEPAGENTS.value
    assert service.calls == [(project["project_id"], "Create a test part.")]
    diagnostics = app.state.project_store.read_diagnostics(project["project_id"])
    assert diagnostics is not None
    assert diagnostics["harness"] == AgentHarness.DEEPAGENTS.value
    assert diagnostics["implementation_version"] == DEEPAGENTS_IMPLEMENTATION_VERSION
    records = app.state.project_store.conversation_log(project["project_id"]).data[
        "records"
    ]
    started_event = next(
        record for record in records if record["type"] == "turn_started"
    )
    assert started_event["payload"]["harness"] == AgentHarness.DEEPAGENTS.value
    assert (
        started_event["payload"]["implementation_version"]
        == DEEPAGENTS_IMPLEMENTATION_VERSION
    )


def test_unsupported_harness_is_rejected_by_request_model(tmp_path: Path) -> None:
    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Invalid"}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": "Invalid", "harness": "unknown"},
    )
    assert response.status_code == 422
    assert client.get(f"/api/projects/{project['project_id']}").json()["state"] == "Draft"
