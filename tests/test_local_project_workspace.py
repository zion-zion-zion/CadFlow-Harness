from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.agent import AgentRunOutcome
from backend.app import create_app


class _LifecycleRunHarness:
    """Keep a lifecycle boundary active without making an Agent claim."""

    def __init__(self) -> None:
        self.started = threading.Event()

    def run(
        self,
        _project_id: str,
        _prompt: str,
        *,
        cancellation_token: object,
        progress_callback: object,
        conversation_log: object,
    ) -> AgentRunOutcome:
        assert conversation_log is not None
        assert callable(progress_callback)
        self.started.set()
        while not getattr(cancellation_token, "cancelled"):
            time.sleep(0.005)
        return AgentRunOutcome(
            validated=False,
            cancelled=True,
            failure_reason="Agent Run stopped by user",
        )


def test_project_catalog_creates_duplicate_names_and_requires_exact_delete_confirmation(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path, run_service=_LifecycleRunHarness())
    client = TestClient(app)

    first = client.post("/api/projects", json={"name": "Bracket / left"}).json()
    second = client.post("/api/projects", json={"name": "Bracket / left"}).json()

    assert first["project_id"] != second["project_id"]
    assert "/" not in first["project_id"]
    assert client.get("/api/projects").json()[0]["name"] == "Bracket / left"

    rejected = client.request(
        "DELETE",
        f"/api/projects/{first['project_id']}",
        json={"name": "wrong"},
    )
    assert rejected.status_code == 409
    assert client.get(f"/api/projects/{first['project_id']}").status_code == 200

    deleted = client.request(
        "DELETE",
        f"/api/projects/{first['project_id']}",
        json={"name": "Bracket / left"},
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/projects/{first['project_id']}").status_code == 404
    assert not (tmp_path / first["project_id"]).exists()


def test_deleting_a_running_project_removes_data_after_lifecycle_cancellation(
    tmp_path: Path,
) -> None:
    service = _LifecycleRunHarness()
    app = create_app(projects_root=tmp_path, run_service=service)
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Active"}).json()

    started = client.post(
        f"/api/projects/{project['project_id']}/run",
        json={"prompt": "Create the active part."},
    )
    assert started.status_code == 202
    assert service.started.wait(1.0)

    deleted = client.request(
        "DELETE",
        f"/api/projects/{project['project_id']}",
        json={"confirm_name": "Active"},
    )

    assert deleted.status_code == 204
    assert app.state.run_coordinator.active_project_id is None
    assert not (tmp_path / project["project_id"]).exists()


def test_workspace_rejects_invalid_prompt_before_starting_and_hides_scene_for_draft(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path, run_service=_LifecycleRunHarness())
    client = TestClient(app)
    project = client.post("/api/projects", json={"name": "Draft"}).json()

    for prompt in ("", " ", "x" * 32_001):
        response = client.post(
            f"/api/projects/{project['project_id']}/run",
            json={"prompt": prompt},
        )
        assert response.status_code == 422

    assert client.get(f"/api/projects/{project['project_id']}/scene").status_code == 404
    assert (
        client.get(f"/api/projects/{project['project_id']}").json()["state"] == "Draft"
    )


def test_fastapi_serves_built_frontend_from_same_origin_when_dist_is_present(
    tmp_path: Path,
) -> None:
    frontend_dist = tmp_path / "dist"
    assets = frontend_dist / "assets"
    assets.mkdir(parents=True)
    (frontend_dist / "index.html").write_text(
        "<main>workspace</main>", encoding="utf-8"
    )
    (frontend_dist / "trace.html").write_text(
        "<main>trace dashboard</main>", encoding="utf-8"
    )
    (assets / "app.js").write_text("console.log('workspace')", encoding="utf-8")

    app = create_app(
        projects_root=tmp_path / "projects",
        frontend_dist=frontend_dist,
        run_service=_LifecycleRunHarness(),
    )
    client = TestClient(app)

    assert client.get("/").text == "<main>workspace</main>"
    assert client.get("/trace").text == "<main>trace dashboard</main>"
    assert (
        client.get("/trace/0123456789abcdef0123456789abcdef").text
        == "<main>trace dashboard</main>"
    )
    assert client.get("/unrelated-client-route").text == "<main>workspace</main>"
    assert client.get("/assets/app.js").text == "console.log('workspace')"
    assert "access-control-allow-origin" not in client.options("/").headers
