from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent import resolve_agent_run_timeout_seconds
from backend.agent import AgentSettings
from backend.app import create_app
from backend.projects import ProjectState
from backend.repair_state import RunIdentity
from backend.scene_validation import validate_scene_artifact
from tests.support import process_exists


LIVE_FLANGE_PROMPT = (
    "创建一个外径 80 mm、厚 10 mm、中心孔直径 30 mm、节圆直径 60 mm、"
    "均布 6 个直径 6 mm 通孔的圆形法兰盘，所有边缘做 1 mm 倒角。"
)
LIVE_CANCELLATION_PROMPT = (
    "创建一个 20 mm 立方体。为了验证 Stop 控制，请在 Model Source 中使用 Python "
    "标准库 time.sleep(30) 放在创建最终 Solid 之前，然后继续生成一个有效的单 Solid "
    "并导出 canonical Scene Artifact。"
)
LIVE_AGENT_TIMEOUT_SECONDS = resolve_agent_run_timeout_seconds()
LIVE_CONTRACT_PLATE_PROMPT = (
    "Create one rectangular plate measuring 60 mm long, 40 mm wide, and 8 mm "
    "thick. Add exactly two 6 mm diameter through holes. Place both hole centers "
    "on the plate width centerline, 30 mm apart along the length, symmetric about "
    "the plate center. Return one rigid single solid with no extra features."
)


def _wait_for_terminal_project(
    client: TestClient,
    project_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200
        project = response.json()
        if project["state"] in {
            ProjectState.SUCCEEDED.value,
            ProjectState.FAILED.value,
            ProjectState.STOPPED.value,
        }:
            return project
        time.sleep(0.25)
    raise AssertionError(f"live Project did not finish: {project_id}")


@pytest.mark.live_agent
def test_live_agent_flange_smoke_crosses_service_and_scene_viewer_path(
    tmp_path: Path,
) -> None:
    """Use the configured real Agent and observe only public result boundaries."""

    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Live flange"}).json()

    started = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": LIVE_FLANGE_PROMPT},
    )
    assert started.status_code == 202

    finished = _wait_for_terminal_project(
        client,
        project["project_id"],
        timeout_seconds=LIVE_AGENT_TIMEOUT_SECONDS + 30.0,
    )
    assert finished["state"] == ProjectState.SUCCEEDED.value
    assert finished["scene_available"] is True

    scene_response = client.get(f"/api/projects/{project['project_id']}/scene")
    assert scene_response.status_code == 200
    assert scene_response.headers["content-type"].startswith("application/zip")
    scene_path = tmp_path / "live-model.scene.zip"
    scene_path.write_bytes(scene_response.content)
    parsed = validate_scene_artifact(scene_path)
    assert parsed.valid is True
    assert parsed.glb_asset_count >= 1
    assert parsed.model_json_present is False


@pytest.mark.live_agent
def test_live_agent_contract_repair_state_and_review_production_path(
    tmp_path: Path,
) -> None:
    """Exercise the production path and verify every progressive-repair artifact."""

    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "Contract plate E2E"}
    ).json()
    project_id = str(project["project_id"])

    started = client.post(
        f"/api/projects/{project_id}/run",
        json={"prompt": LIVE_CONTRACT_PLATE_PROMPT},
    )
    assert started.status_code == 202
    finished = _wait_for_terminal_project(
        client,
        project_id,
        timeout_seconds=LIVE_AGENT_TIMEOUT_SECONDS + 30.0,
    )

    assert finished["state"] == ProjectState.SUCCEEDED.value
    store = app.state.project_store
    project_dir = store.project_directory(project_id)
    model_source = (project_dir / "code" / "model.py").read_text(encoding="utf-8")
    assert model_source.strip()
    assert "build_model" in model_source

    turns = store.conversation_turns(project_id)
    assert len(turns) == 1
    turn = turns[0]
    identity = RunIdentity(
        project_id=project_id,
        turn_id=str(turn["turn_id"]),
        request_id=str(turn["request_id"]),
        request_text=LIVE_CONTRACT_PLATE_PROMPT,
    )
    repair_state = store.repair_state(project_id)
    contract = repair_state.design_contract(identity)
    assert contract is not None
    assert contract.task_type == "single_part"
    assert contract.explicit_requirements

    attempts = repair_state.attempts(identity)
    validations = [item for item in attempts if item.attempt_kind == "validation"]
    reviews = [item for item in attempts if item.attempt_kind == "review"]
    assert validations
    assert validations[-1].validation_status == "passed"
    assert reviews
    assert reviews[-1].review_status == "pass"

    last_passing = repair_state.last_passing_source()
    assert last_passing is not None
    assert last_passing.source_revision == validations[-1].source_revision
    assert last_passing.archive_path.is_file()
    import zipfile

    with zipfile.ZipFile(last_passing.archive_path) as archive:
        assert archive.testzip() is None
        assert "code/model.py" in archive.namelist()
        assert archive.read("code/model.py").decode("utf-8") == model_source

    diagnostics = store.read_diagnostics(project_id)
    assert diagnostics is not None
    assert diagnostics["cad_execution_count"] == len(validations)
    assert diagnostics["execution_result"]["product_validation_status"] == "Passed"
    assert diagnostics["review_result"]["status"] == "pass"
    assert diagnostics["design_contract"]["contract_id"] == contract.contract_id
    assert len(diagnostics["attempt_ledger"]) == len(attempts)

    product = store.product_artifact(project_id)
    product.require_complete()
    scene = store.scene_artifact(project_id)
    parsed = validate_scene_artifact(scene)
    assert parsed.valid is True
    assert parsed.glb_asset_count >= 1

    api_key = AgentSettings.from_environment().api_key
    for path in project_dir.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt", ".py"}:
            assert api_key not in path.read_text(encoding="utf-8", errors="replace")


@pytest.mark.live_agent
def test_live_agent_stop_observes_stopped_and_terminated_cad_child(
    tmp_path: Path,
) -> None:
    """Cancel a real Agent after its generated Model Source enters CAD execution."""

    app = create_app(projects_root=tmp_path)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Live cancellation"}).json()
    started = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": LIVE_CANCELLATION_PROMPT},
    )
    assert started.status_code == 202

    coordinator = app.state.run_coordinator
    deadline = time.monotonic() + LIVE_AGENT_TIMEOUT_SECONDS
    process_id: int | None = None
    while time.monotonic() < deadline:
        process_id = coordinator.active_process_id
        if process_id is not None:
            break
        state = client.get(f"/api/projects/{project['project_id']}").json()["state"]
        if state in {
            ProjectState.SUCCEEDED.value,
            ProjectState.FAILED.value,
            ProjectState.STOPPED.value,
        }:
            break
        time.sleep(0.25)

    assert process_id is not None, (
        "live cancellation must observe the generated Model Source's active CAD child; "
        "the real Agent finished before the cancellation seam"
    )
    stopped = client.post(f"/api/projects/{project['project_id']}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == ProjectState.STOPPED.value
    assert coordinator.active_project_id is None
    assert coordinator.active_process_id is None

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and process_exists(process_id):
        time.sleep(0.01)
    assert process_exists(process_id) is False
    assert client.get(f"/api/projects/{project['project_id']}/scene").status_code == 404
