from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.agent import AgentRunService
from backend.app import app as default_app
from backend.app import create_app
from backend.projects import ProjectStore


_OPENAPI_SHA256 = "27885d921173081f7cd9d7173b0401a0e48d308510926cd81e2bc2fd6e473244"
_API_ROUTES = {
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/{project_id}"),
    ("DELETE", "/api/projects/{project_id}"),
    ("POST", "/api/projects/{project_id}/run"),
    ("GET", "/api/projects/{project_id}/messages"),
    ("POST", "/api/projects/{project_id}/messages"),
    ("DELETE", "/api/projects/{project_id}/conversation"),
    ("POST", "/api/projects/{project_id}/stop"),
    ("GET", "/api/projects/{project_id}/scene"),
    ("GET", "/api/projects/{project_id}/product"),
    ("GET", "/api/projects/{project_id}/product/manifest"),
    ("GET", "/api/projects/{project_id}/product/files/{role}"),
    ("GET", "/api/projects/{project_id}/product/part-step"),
    ("GET", "/api/traces"),
    ("GET", "/api/projects/{project_id}/events"),
    ("GET", "/api/projects/{project_id}/trace"),
    ("GET", "/api/projects/{project_id}/trace/download"),
    ("GET", "/api/projects/{project_id}/trace/events"),
    ("GET", "/api/projects/{project_id}/preview/status"),
    ("GET", "/api/projects/{project_id}/preview"),
    ("POST", "/api/projects/{project_id}/preview/retry"),
    ("POST", "/api/projects/{project_id}/preview/pause"),
}


class _RecordingScheduler:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _openapi_sha256(app: FastAPI) -> str:
    payload = json.dumps(
        app.openapi(), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def test_route_table_and_openapi_match_the_pre_refactor_contract(
    tmp_path: Path,
) -> None:
    app = create_app(projects_root=tmp_path, frontend_dist=tmp_path / "missing")
    api_routes = [
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/")
        for method in route.methods
    ]

    assert set(api_routes) == _API_ROUTES
    assert len(api_routes) == len(_API_ROUTES)
    assert _openapi_sha256(app) == _OPENAPI_SHA256


def test_create_app_reuses_injected_instances_and_closes_scheduler_once(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path / "projects")
    run_service = object()
    scheduler = _RecordingScheduler()
    app = create_app(
        store=store,
        repo_root=tmp_path / "repo",
        run_service=run_service,  # type: ignore[arg-type]
        preview_scheduler=scheduler,  # type: ignore[arg-type]
        frontend_dist=tmp_path / "missing",
    )

    assert app.state.project_store is store
    assert app.state.run_coordinator.store is store
    assert app.state.run_coordinator.run_service is run_service
    assert app.state.run_coordinator.events is app.state.event_store
    assert app.state.preview_scheduler is scheduler
    assert app.state.run_coordinator.preview_scheduler is scheduler
    assert scheduler.close_calls == 0

    with TestClient(app):
        assert scheduler.close_calls == 0

    assert scheduler.close_calls == 1


def test_create_app_forwards_default_service_configuration(tmp_path: Path) -> None:
    repo_root = tmp_path / "repository"

    def settings_factory() -> object:
        return object()

    def agent_factory(**_kwargs: object) -> object:
        return object()

    app = create_app(
        projects_root=tmp_path / "projects",
        repo_root=repo_root,
        settings_factory=settings_factory,  # type: ignore[arg-type]
        agent_factory=agent_factory,  # type: ignore[arg-type]
        frontend_dist=tmp_path / "missing",
    )
    service = app.state.run_coordinator.run_service

    assert isinstance(default_app, FastAPI)
    assert isinstance(service, AgentRunService)
    assert service.store is app.state.project_store
    assert service.repo_root == repo_root.resolve()
    assert service.settings_factory is settings_factory
    assert service.agent_factory is agent_factory

    with TestClient(app):
        pass


def test_frontend_fallback_does_not_capture_api_or_escape_dist(
    tmp_path: Path,
) -> None:
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    frontend_dist.joinpath("index.html").write_text("workspace", encoding="utf-8")
    outside = tmp_path / "outside.html"
    outside.write_text("outside", encoding="utf-8")
    app = create_app(
        projects_root=tmp_path / "projects",
        frontend_dist=frontend_dist,
    )

    with TestClient(app) as client:
        api_response = client.get("/api/not-a-route")
        traversal_response = client.get("/%2e%2e/outside.html")
        fallback_response = client.get("/workspace/route")

    assert api_response.status_code == 404
    assert api_response.json() == {"detail": "Not Found"}
    assert traversal_response.status_code == 404
    assert traversal_response.json() == {"detail": "Not Found"}
    assert fallback_response.status_code == 200
    assert fallback_response.text == "workspace"
