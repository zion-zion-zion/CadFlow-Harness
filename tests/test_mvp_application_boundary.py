from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi.testclient import TestClient

from backend.agent import AgentRunOutcome
from backend.app import create_app
from backend.cad_executor import CADExecutor, CancellationToken
from backend.cad_review import ReviewResult
from backend.events import ProgressUpdate
from backend.model_source import create_model_source
from backend.projects import ProjectState, ProjectStore
from backend.scene_validation import validate_scene_artifact
from tests.support import process_exists


def _wait_for_state(
    client: TestClient,
    project_id: str,
    expected: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200
        latest = response.json()
        if latest["state"] == expected:
            return latest
        time.sleep(0.02)
    raise AssertionError(
        f"Project {project_id} did not reach {expected}; last payload was {latest}"
    )


def _wait_for_active_process(
    client: TestClient,
    project_id: str,
    app: object,
    *,
    timeout_seconds: float = 5.0,
) -> int:
    coordinator = app.state.run_coordinator  # type: ignore[attr-defined]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        process_id = coordinator.active_process_id
        if process_id is not None:
            return process_id
        payload = client.get(f"/api/projects/{project_id}").json()
        if payload["state"] != ProjectState.RUNNING.value:
            break
        time.sleep(0.01)
    raise AssertionError("the deterministic CAD child process did not become active")


def _sse_payloads(body: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        value = json.loads(line.removeprefix("data: "))
        assert isinstance(value, dict)
        payloads.append(value)
    return payloads


class _DeterministicSuccessHarness:
    """Run the real CAD executor without pretending to be an autonomous Agent.

    This harness only covers the HTTP, persistence, event, and Scene boundaries.
    Live tests below are the only tests that make claims about model generation.
    """

    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root

    def run(
        self,
        project_id: str,
        _prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: Callable[[ProgressUpdate], None],
        prompt_submitted: bool,
    ) -> AgentRunOutcome:
        assert prompt_submitted is True
        project_dir = self.projects_root / project_id
        progress_callback(ProgressUpdate(stage="reading_references", tool="reference"))
        scaffold = create_model_source(project_dir)
        scaffold.model_path.write_text(
            """import cadflow as cad

def build_model(model: cad.Model):
    return model.box(width=10.0, depth=10.0, height=10.0)
""",
            encoding="utf-8",
        )
        progress_callback(ProgressUpdate(stage="writing_model", tool="model_source"))
        result = CADExecutor().execute(
            project_dir,
            timeout_seconds=30.0,
            cancellation_token=cancellation_token,
        )
        progress_callback(
            ProgressUpdate(
                stage="executing",
                tool="cad",
                attempt=1,
                result="CAD execution completed",
            )
        )
        assert scaffold.scene_path == project_dir / "artifacts" / "model.scene.zip"
        assert result.is_validated_product
        review = ReviewResult(
            status="pass",
            summary="The deterministic test block matches the request.",
            checked_requirements=("deterministic test block",),
            model_sha256=result.review_model_sha256,
        )
        return AgentRunOutcome(
            validated=True,
            failure_reason=result.error,
            execution_result=result,
            execution_results=(result,),
            review_result=review,
        )


class _DeterministicFailureHarness:
    def run(
        self,
        _project_id: str,
        _prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: Callable[[ProgressUpdate], None],
        prompt_submitted: bool,
    ) -> AgentRunOutcome:
        assert prompt_submitted is True
        assert cancellation_token.cancelled is False
        progress_callback(ProgressUpdate(stage="executing", tool="cad", attempt=1))
        return AgentRunOutcome(
            validated=False,
            failure_reason="deterministic validation failure",
        )


class _SequentialTokenUsageHarness:
    def __init__(self) -> None:
        self._turn = 0

    def run(self, *_args: object, **_kwargs: object) -> AgentRunOutcome:
        usages = (
            {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "uncached_input_tokens": 60,
                "output_tokens": 25,
                "total_tokens": 125,
            },
            {
                "input_tokens": 60,
                "cached_input_tokens": 10,
                "uncached_input_tokens": 50,
                "output_tokens": 15,
                "total_tokens": 75,
            },
        )
        usage = usages[self._turn]
        self._turn += 1
        return AgentRunOutcome(
            validated=False,
            failure_reason="deterministic validation failure",
            token_usage=usage,
        )


class _ValidatedWithoutArtifactHarness:
    def run(self, *_args: object, **_kwargs: object) -> AgentRunOutcome:
        return AgentRunOutcome(validated=True)


class _SuccessFailureSuccessHarness:
    def __init__(self, projects_root: Path) -> None:
        self.success = _DeterministicSuccessHarness(projects_root)
        self.failure = _DeterministicFailureHarness()
        self.call_count = 0

    def run(self, project_id: str, prompt: str, **kwargs: object) -> AgentRunOutcome:
        self.call_count += 1
        harness = self.failure if self.call_count == 2 else self.success
        return harness.run(
            project_id,
            prompt,
            cancellation_token=kwargs["cancellation_token"],  # type: ignore[arg-type]
            progress_callback=kwargs["progress_callback"],  # type: ignore[arg-type]
            prompt_submitted=bool(kwargs["prompt_submitted"]),
        )


class _DeterministicBlockingCadHarness:
    """Exercise HTTP Stop/Delete against a real cancellable CAD child only."""

    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root
        self.started = threading.Event()

    def run(
        self,
        project_id: str,
        _prompt: str,
        *,
        cancellation_token: CancellationToken,
        progress_callback: Callable[[ProgressUpdate], None],
        prompt_submitted: bool,
    ) -> AgentRunOutcome:
        assert prompt_submitted is True
        project_dir = self.projects_root / project_id
        scaffold = create_model_source(project_dir)
        scaffold.model_path.write_text(
            "import time\n\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        progress_callback(ProgressUpdate(stage="executing", tool="cad", attempt=1))
        self.started.set()
        result = CADExecutor().execute(
            project_dir,
            timeout_seconds=30.0,
            cancellation_token=cancellation_token,
        )
        return AgentRunOutcome(
            validated=False,
            cancelled=result.status == "cancelled",
            failure_reason=result.error or "deterministic CAD run stopped",
            execution_result=result,
            execution_results=(result,),
        )


def test_application_uses_cadflow_agent_product_name(tmp_path: Path) -> None:
    app = create_app(
        projects_root=tmp_path,
        run_service=_DeterministicFailureHarness(),
    )

    assert app.title == "CadFlowAgent"


def test_project_api_exposes_only_curated_terminal_run_metrics(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    measured = store.create_project("Measured failure")
    store.submit_prompt(measured.project_id, "Create a measured part.")
    store.mark_failed(
        measured.project_id,
        "expected failure",
        {
            "duration_seconds": 42.75,
            "token_usage": {
                "input_tokens": 1200,
                "cached_input_tokens": 800,
                "uncached_input_tokens": 400,
                "output_tokens": 345,
                "total_tokens": 1545,
                "provider_metadata": "must not be exposed",
            },
            "raw_provider_response": "must not be exposed",
        },
    )
    invalid = store.create_project("Invalid legacy metrics")
    store.submit_prompt(invalid.project_id, "Create another part.")
    store.mark_failed(
        invalid.project_id,
        "legacy failure",
        {
            "duration_seconds": True,
            "token_usage": {
                "input_tokens": -1,
                "output_tokens": "20",
                "total_tokens": False,
            },
        },
    )
    draft = store.create_project("Draft")
    (store.project_directory(draft.project_id) / "diagnostics.json").write_text(
        json.dumps(
            {
                "duration_seconds": 999.0,
                "token_usage": {
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "total_tokens": 3,
                },
            }
        ),
        encoding="utf-8",
    )

    app = create_app(store=store, run_service=_DeterministicFailureHarness())
    client = TestClient(app)

    measured_payload = client.get(
        f"/api/projects/{measured.project_id}"
    ).json()
    assert measured_payload["duration_seconds"] == 42.75
    assert measured_payload["token_usage"] == {
        "total_tokens": 1545,
        "input_tokens": 1200,
        "cached_input_tokens": 800,
        "uncached_input_tokens": 400,
        "output_tokens": 345,
    }
    assert "raw_provider_response" not in measured_payload
    assert "provider_metadata" not in json.dumps(measured_payload)

    payloads = {
        item["project_id"]: item for item in client.get("/api/projects").json()
    }
    assert payloads[measured.project_id]["token_usage"] == {
        "total_tokens": 1545,
        "input_tokens": 1200,
        "cached_input_tokens": 800,
        "uncached_input_tokens": 400,
        "output_tokens": 345,
    }
    assert payloads[invalid.project_id]["duration_seconds"] is None
    assert payloads[invalid.project_id]["token_usage"] is None
    assert payloads[draft.project_id]["diagnostics_available"] is True
    assert payloads[draft.project_id]["duration_seconds"] is None
    assert payloads[draft.project_id]["token_usage"] is None


def test_http_boundary_persists_success_events_and_scene_artifact(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    app = create_app(
        projects_root=projects_root,
        run_service=_DeterministicSuccessHarness(projects_root),
    )
    client = TestClient(app)

    created = client.post("/api/projects", json={"name": "Flange Demo"})
    assert created.status_code == 201
    project = created.json()
    project_id = project["project_id"]
    assert project["state"] == ProjectState.DRAFT.value

    started = client.post(
        f"/api/projects/{project_id}/run",
        json={"prompt": "Create a deterministic test part."},
    )
    assert started.status_code == 202
    assert started.json()["state"] == ProjectState.RUNNING.value
    project_dir = projects_root / project_id
    assert (project_dir / "prompt.txt").read_text(encoding="utf-8") == (
        "Create a deterministic test part."
    )

    succeeded = _wait_for_state(client, project_id, ProjectState.SUCCEEDED.value)
    assert succeeded["prompt"] == "Create a deterministic test part."
    assert succeeded["scene_available"] is True
    assert succeeded["product_available"] is True
    assert succeeded["result_kind"] == "part"
    assert succeeded["product_status"] == "Accepted"
    assert succeeded["diagnostics_available"] is True

    product_response = client.get(f"/api/projects/{project_id}/product")
    assert product_response.status_code == 200
    assert product_response.json()["status"] == "Accepted"

    scene_response = client.get(f"/api/projects/{project_id}/scene")
    assert scene_response.status_code == 200
    assert scene_response.headers["content-type"].startswith("application/zip")
    downloaded_scene = tmp_path / "downloaded.scene.zip"
    downloaded_scene.write_bytes(scene_response.content)
    scene_result = validate_scene_artifact(downloaded_scene)
    assert scene_result.valid is True
    assert scene_result.glb_asset_count == 2
    assert scene_result.model_json_present is False

    events_response = client.get(
        f"/api/projects/{project_id}/events?follow=false",
    )
    assert events_response.status_code == 200
    events = _sse_payloads(events_response.text)
    event_ids = [int(event["id"]) for event in events]
    assert event_ids == list(range(1, len(event_ids) + 1))
    assert {event["stage"] for event in events} >= {
        "preparing",
        "reading_references",
        "writing_model",
        "executing",
        "completed",
    }
    assert all("stdout" not in event and "stderr" not in event for event in events)

    replay = client.get(
        f"/api/projects/{project_id}/events?follow=false",
        headers={"Last-Event-ID": str(event_ids[0])},
    )
    replayed = _sse_payloads(replay.text)
    assert [int(event["id"]) for event in replayed] == event_ids[1:]


def test_http_boundary_exposes_failed_state_and_accepts_a_second_run(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    app = create_app(
        projects_root=projects_root,
        run_service=_DeterministicFailureHarness(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Failure"}).json()

    started = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": "Create a part that will fail validation."},
    )
    assert started.status_code == 202
    failed = _wait_for_state(client, project["project_id"], ProjectState.FAILED.value)
    assert failed["failure_reason"] == "deterministic validation failure"
    assert failed["scene_available"] is False
    assert client.get(f"/api/projects/{project['project_id']}/scene").status_code == 404

    assert app.state.run_coordinator.wait_for_idle(1.0)
    second_run = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": "Try a corrected follow-up."},
    )
    assert second_run.status_code == 202


def test_project_token_usage_accumulates_across_multiple_turns(
    tmp_path: Path,
) -> None:
    app = create_app(
        projects_root=tmp_path / "projects",
        run_service=_SequentialTokenUsageHarness(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Token totals"}).json()
    project_id = project["project_id"]

    first = client.post(
        f"/api/projects/{project_id}/run",
        json={"prompt": "Create the first version."},
    )
    assert first.status_code == 202
    first_result = _wait_for_state(client, project_id, ProjectState.FAILED.value)
    assert first_result["token_usage"]["total_tokens"] == 125
    assert app.state.run_coordinator.wait_for_idle(1.0)

    second = client.post(
        f"/api/projects/{project_id}/run",
        json={"prompt": "Refine the first version."},
    )
    assert second.status_code == 202
    second_result = _wait_for_state(client, project_id, ProjectState.FAILED.value)

    assert second_result["token_usage"] == {
        "total_tokens": 200,
        "input_tokens": 160,
        "cached_input_tokens": 50,
        "uncached_input_tokens": 110,
        "output_tokens": 40,
    }


def test_message_turn_fails_when_a_validated_run_has_no_artifact(tmp_path: Path) -> None:
    app = create_app(
        projects_root=tmp_path,
        run_service=_ValidatedWithoutArtifactHarness(),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Missing artifact"}).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/messages",
        json={"message": "Create a model.", "request_id": "missing-artifact"},
    )

    assert response.status_code == 200
    turn = response.json()["turn"]
    assert turn["status"] == "failed"
    assert "complete product artifact" in turn["error"]
    assert turn["artifact_version"] is None


def test_message_api_persists_multiturn_history_idempotency_and_artifact_versions(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    harness = _SuccessFailureSuccessHarness(projects_root)
    app = create_app(projects_root=projects_root, run_service=harness)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Conversation"}).json()
    project_id = project["project_id"]

    first = client.post(
        f"/api/projects/{project_id}/messages",
        json={"message": "Create a bracket.", "request_id": "request-1"},
    )
    assert first.status_code == 200
    assert first.json()["turn"]["status"] == "succeeded"
    assert first.json()["artifact"]["version"] == 1

    second = client.post(
        f"/api/projects/{project_id}/messages",
        json={"message": "Make the holes larger.", "request_id": "request-2"},
    )
    assert second.status_code == 200
    second_turn = second.json()["turn"]
    assert second_turn["status"] == "failed"
    assert second.json()["artifact"] == {"version": 1, "scene_available": True}
    assert client.get(f"/api/projects/{project_id}/scene").status_code == 200

    duplicate = client.post(
        f"/api/projects/{project_id}/messages",
        json={"message": "Ignored duplicate body.", "request_id": "request-2"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["turn"]["turn_id"] == second_turn["turn_id"]
    assert harness.call_count == 2

    retry = client.post(
        f"/api/projects/{project_id}/messages",
        json={
            "message": "Make the holes larger.",
            "request_id": "request-3",
            "retry_of": second_turn["turn_id"],
        },
    )
    assert retry.status_code == 200
    assert retry.json()["turn"]["status"] == "succeeded"
    assert retry.json()["turn"]["retry_of"] == second_turn["turn_id"]
    assert retry.json()["artifact"]["version"] == 2

    conversation = client.get(f"/api/projects/{project_id}/messages").json()
    assert [turn["status"] for turn in conversation["turns"]] == [
        "succeeded",
        "failed",
        "succeeded",
    ]
    assert (projects_root / project_id / "conversation.jsonl").is_file()
    assert not (projects_root / project_id / "agent-run.jsonl").exists()


def test_clear_conversation_removes_history_and_artifacts(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    app = create_app(
        projects_root=projects_root,
        run_service=_DeterministicSuccessHarness(projects_root),
    )
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Clear Me"}).json()
    project_id = project["project_id"]
    assert client.post(
        f"/api/projects/{project_id}/messages",
        json={"message": "Create a block.", "request_id": "request-1"},
    ).status_code == 200

    cleared = client.request(
        "DELETE",
        f"/api/projects/{project_id}/conversation",
        json={"confirm_name": "Clear Me"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["state"] == ProjectState.DRAFT.value
    assert cleared.json()["turn_count"] == 0
    assert cleared.json()["scene_available"] is False
    assert client.get(f"/api/projects/{project_id}/messages").json()["turns"] == []


def test_http_boundary_stop_conflict_and_delete_remove_project_data(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    harness = _DeterministicBlockingCadHarness(projects_root)
    app = create_app(projects_root=projects_root, run_service=harness)
    client = TestClient(app)
    first = client.post("/api/projects", json={"name": "Active"}).json()
    second = client.post("/api/projects", json={"name": "Waiting"}).json()

    start_first = client.post(
        f"/api/projects/{first['project_id']}/run",
        json={"prompt": "Create an active test part."},
    )
    assert start_first.status_code == 202
    assert harness.started.wait(1.0)
    first_process = _wait_for_active_process(client, first["project_id"], app)

    conflict = client.post(
        f"/api/projects/{second['project_id']}/run",
        json={"prompt": "This must wait."},
    )
    assert conflict.status_code == 409
    assert client.get(f"/api/projects/{second['project_id']}").json()["state"] == (
        ProjectState.DRAFT.value
    )

    stopped = client.post(f"/api/projects/{first['project_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == ProjectState.STOPPED.value
    assert app.state.run_coordinator.active_project_id is None
    assert app.state.run_coordinator.active_process_id is None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and process_exists(first_process):
        time.sleep(0.01)
    assert process_exists(first_process) is False
    assert client.get(f"/api/projects/{first['project_id']}/scene").status_code == 404

    third = client.post("/api/projects", json={"name": "Delete Me"}).json()
    start_third = client.post(
        f"/api/projects/{third['project_id']}/run",
        json={"prompt": "Create another active test part."},
    )
    assert start_third.status_code == 202
    assert harness.started.wait(1.0)
    third_process = _wait_for_active_process(client, third["project_id"], app)

    deleted = client.request(
        "DELETE",
        f"/api/projects/{third['project_id']}",
        json={"name": "Delete Me"},
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{third['project_id']}").status_code == 404
    assert not (projects_root / third["project_id"]).exists()
    assert app.state.run_coordinator.active_project_id is None
    assert process_exists(third_process) is False


def test_http_restart_reconstructs_terminal_projects_and_recovers_running_state(
    tmp_path: Path,
) -> None:
    projects_root = tmp_path / "projects"
    first_app = create_app(
        projects_root=projects_root,
        run_service=_DeterministicSuccessHarness(projects_root),
    )
    first_client = TestClient(first_app)
    interrupted = first_client.post(
        "/api/projects", json={"name": "Interrupted"}
    ).json()
    first_app.state.project_store.submit_prompt(
        interrupted["project_id"], "A run interrupted by restart."
    )

    restarted = create_app(
        projects_root=projects_root,
        run_service=_DeterministicSuccessHarness(projects_root),
    )
    restarted_client = TestClient(restarted)
    recovered = restarted_client.get(
        f"/api/projects/{interrupted['project_id']}"
    ).json()
    assert recovered["state"] == ProjectState.FAILED.value
    assert recovered["failure_reason"] == "Agent Run was interrupted by service restart"
    assert recovered["diagnostics_available"] is True
    assert restarted.state.run_coordinator.active_project_id is None
    assert (
        restarted_client.get(
            f"/api/projects/{interrupted['project_id']}/scene"
        ).status_code
        == 404
    )

    completed = restarted_client.post(
        "/api/projects", json={"name": "Completed"}
    ).json()
    assert (
        restarted_client.post(
            f"/api/projects/{completed['project_id']}/run",
            json={"prompt": "A completed persisted test part."},
        ).status_code
        == 202
    )
    _wait_for_state(
        restarted_client, completed["project_id"], ProjectState.SUCCEEDED.value
    )

    restarted_again = create_app(
        projects_root=projects_root,
        run_service=_DeterministicSuccessHarness(projects_root),
    )
    reloaded = restarted_again.state.project_store.get_project(completed["project_id"])
    assert reloaded.state is ProjectState.SUCCEEDED
    assert restarted_again.state.project_store.scene_artifact(
        completed["project_id"]
    ).is_file()
