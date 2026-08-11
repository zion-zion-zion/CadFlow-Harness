from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent import MAX_AGENT_RUN_SECONDS
from backend.app import create_app
from backend.projects import ProjectState
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
        timeout_seconds=MAX_AGENT_RUN_SECONDS + 30.0,
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
    assert parsed.model_json_present is True


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
    deadline = time.monotonic() + MAX_AGENT_RUN_SECONDS
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
