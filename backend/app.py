"""FastAPI boundary for the observable, cancellable local Agent Run."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import AgentRunService, AgentSettings, ReferenceGroundedAgent
from .events import ProgressEventStore
from .projects import (
    ProjectError,
    ProjectNotFoundError,
    ProjectStateError,
    ProjectStore,
    PromptValidationError,
)
from .run_coordinator import AgentRunCoordinator, RunConflictError


KEEPALIVE_SECONDS = 15.0
DEFAULT_PROJECTS_ROOT = Path(
    os.environ.get("TEXT_TO_CAD_PROJECTS_ROOT", "output/projects")
)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)


class RunProjectRequest(BaseModel):
    prompt: str


class DeleteProjectRequest(BaseModel):
    """Accept the UI's name confirmation without making it a Project ID."""

    name: str | None = Field(default=None, min_length=1)
    confirmation: str | None = Field(default=None, min_length=1)
    confirm_name: str | None = Field(default=None, min_length=1)

    @property
    def value(self) -> str | None:
        return self.confirmation or self.confirm_name or self.name


def create_app(
    *,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    repo_root: str | Path | None = None,
    store: ProjectStore | None = None,
    run_service: Any | None = None,
    settings_factory: Callable[[], AgentSettings] | None = None,
    agent_factory: Callable[..., ReferenceGroundedAgent] = ReferenceGroundedAgent,
    frontend_dist: str | Path | None = None,
) -> FastAPI:
    project_store = store or ProjectStore(projects_root)
    event_store = ProgressEventStore(project_store.root)
    resolved_repo_root = (
        Path(repo_root).expanduser().resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    service = run_service or AgentRunService(
        store=project_store,
        repo_root=resolved_repo_root,
        settings_factory=settings_factory,
        agent_factory=agent_factory,
    )
    coordinator = AgentRunCoordinator(
        store=project_store,
        repo_root=resolved_repo_root,
        event_store=event_store,
        run_service=service,
    )
    coordinator.recover_interrupted_runs()

    app = FastAPI(title="CadFlowAgent")
    app.state.project_store = project_store
    app.state.event_store = event_store
    app.state.run_coordinator = coordinator

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, object]]:
        return [
            _project_payload(project_store, project)
            for project in project_store.list_projects()
        ]

    @app.post("/api/projects", status_code=201)
    def create_project(request: CreateProjectRequest) -> dict[str, object]:
        try:
            project = project_store.create_project(request.name)
        except ProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _project_payload(project_store, project)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, object]:
        project = _get_project_or_404(project_store, project_id)
        return _project_payload(project_store, project)

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, request: DeleteProjectRequest) -> Response:
        project = _get_project_or_404(project_store, project_id)
        confirmation = request.value
        if confirmation is None:
            raise HTTPException(
                status_code=422,
                detail="Project name confirmation is required",
            )
        if confirmation != project.name:
            raise HTTPException(
                status_code=409,
                detail="Project name confirmation does not match",
            )
        try:
            coordinator.delete(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/run", status_code=202)
    def start_run(project_id: str, request: RunProjectRequest) -> dict[str, object]:
        try:
            project = coordinator.start(project_id, request.prompt)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PromptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _project_payload(project_store, project)

    @app.post("/api/projects/{project_id}/stop")
    def stop_run(project_id: str) -> dict[str, object]:
        try:
            project = coordinator.stop(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _project_payload(project_store, project)

    @app.get("/api/projects/{project_id}/events")
    async def project_events(
        request: Request,
        project_id: str,
        last_event_id_header: str | None = Header(default=None, alias="Last-Event-ID"),
        last_event_id: int | None = Query(default=None, ge=0),
        follow: bool = Query(default=True),
    ) -> StreamingResponse:
        _get_project_or_404(project_store, project_id)
        cursor = _parse_last_event_id(last_event_id_header, last_event_id)

        async def stream():
            nonlocal cursor
            while True:
                if await request.is_disconnected():
                    return
                pending = event_store.read_after(project_id, cursor)
                if pending:
                    for event in pending:
                        cursor = event.event_id
                        yield event.to_sse()
                else:
                    yield event_store.keepalive()
                if not follow:
                    return
                if await request.is_disconnected():
                    return
                await asyncio.to_thread(
                    event_store.wait_for_events,
                    project_id,
                    cursor,
                    KEEPALIVE_SECONDS,
                )

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/projects/{project_id}/scene")
    def project_scene(project_id: str) -> FileResponse:
        _get_project_or_404(project_store, project_id)
        try:
            artifact = project_store.scene_artifact(project_id)
        except ProjectStateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            artifact,
            media_type="application/zip",
            filename="model.scene.zip",
        )

    _mount_frontend(app, frontend_dist)
    return app


def _get_project_or_404(store: ProjectStore, project_id: str):
    try:
        return store.get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _project_payload(store: ProjectStore, project: Any) -> dict[str, object]:
    scene_available = False
    if project.state.value == "Succeeded":
        try:
            scene_available = store.scene_artifact(project.project_id).is_file()
        except ProjectStateError:
            scene_available = False
    return {
        "project_id": project.project_id,
        "name": project.name,
        "state": project.state.value,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "prompt": project.prompt,
        "failure_reason": project.failure_reason,
        "scene_available": scene_available,
        "diagnostics_available": store.read_diagnostics(project.project_id) is not None,
    }


def _parse_last_event_id(header: str | None, query: int | None) -> int:
    raw = header if header is not None else query
    if raw is None:
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Last-Event-ID must be an integer"
        ) from exc
    if value < 0:
        raise HTTPException(
            status_code=400, detail="Last-Event-ID must be non-negative"
        )
    return value


def _mount_frontend(app: FastAPI, frontend_dist: str | Path | None) -> None:
    """Serve the built Vite app from the same origin when it is available."""

    candidate = (
        Path(frontend_dist).expanduser().resolve()
        if frontend_dist is not None
        else Path(__file__).resolve().parents[1] / "viewer" / "dist"
    )
    if not candidate.is_dir():
        return
    index = candidate / "index.html"
    if not index.is_file():
        return
    assets = candidate / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index, media_type="text/html")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_route(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        requested = (candidate / path).resolve()
        try:
            requested.relative_to(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not Found") from exc
        if requested.is_file():
            return FileResponse(requested)
        return FileResponse(index, media_type="text/html")


app = create_app()


__all__ = ["KEEPALIVE_SECONDS", "app", "create_app"]
