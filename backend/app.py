"""FastAPI application composition for the local CadFlow Harness workspace."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI

from .agent import AgentRunService, AgentSettings, ReferenceGroundedAgent
from .api_artifacts import create_artifact_router
from .api_observability import KEEPALIVE_SECONDS, create_observability_router
from .api_preview import create_preview_router
from .api_projects import create_project_router
from .events import ProgressEventStore
from .frontend import mount_frontend
from .live_preview import LivePreviewScheduler
from .projects import ProjectStore
from .run_coordinator import AgentRunCoordinator


DEFAULT_PROJECTS_ROOT = Path(
    os.environ.get("TEXT_TO_CAD_PROJECTS_ROOT", "output/projects")
)


def create_app(
    *,
    projects_root: str | Path = DEFAULT_PROJECTS_ROOT,
    repo_root: str | Path | None = None,
    store: ProjectStore | None = None,
    run_service: AgentRunService | None = None,
    settings_factory: Callable[[], AgentSettings] | None = None,
    agent_factory: Callable[..., ReferenceGroundedAgent] = ReferenceGroundedAgent,
    frontend_dist: str | Path | None = None,
    preview_scheduler: LivePreviewScheduler | None = None,
) -> FastAPI:
    project_store = store or ProjectStore(projects_root)
    event_store = ProgressEventStore(project_store.root)
    resolved_repo_root = (
        Path(repo_root).expanduser().resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    scheduler = preview_scheduler or LivePreviewScheduler(
        project_store,
        on_status=lambda project_id, status: event_store.append(
            project_id,
            stage=f"preview_{status.state}",
            tool="preview",
            result=status.error or f"Live preview {status.state}",
            preview_attempt=(
                1 if status.state == "current" and status.revision > 0 else None
            ),
            preview_revision=(
                status.revision
                if status.state == "current" and status.revision > 0
                else None
            ),
            preview_operation=(
                "result"
                if status.state == "current" and status.revision > 0
                else None
            ),
        ),
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
        preview_scheduler=scheduler,
    )
    coordinator.recover_interrupted_runs()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            scheduler.close()

    app = FastAPI(title="CadFlow Harness", lifespan=lifespan)
    app.state.project_store = project_store
    app.state.event_store = event_store
    app.state.run_coordinator = coordinator
    app.state.preview_scheduler = scheduler

    routers = (
        create_project_router(
            project_store=project_store,
            coordinator=coordinator,
        ),
        create_artifact_router(project_store=project_store),
        create_observability_router(
            project_store=project_store,
            event_store=event_store,
        ),
        create_preview_router(
            project_store=project_store,
            scheduler=scheduler,
        ),
    )
    for router in routers:
        # Keep concrete endpoints visible through app.routes for existing callers.
        app.router.routes.extend(router.routes)
    mount_frontend(app, frontend_dist)
    return app


app = create_app()


__all__ = ["KEEPALIVE_SECONDS", "app", "create_app"]
