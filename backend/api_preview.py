"""Live Preview status, artifact, and control HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from .api_models import PreviewPauseRequest
from .api_payloads import get_project_or_404
from .live_preview import LivePreviewScheduler, LivePreviewStore
from .previews import PreviewError
from .projects import ProjectState, ProjectStore


def create_preview_router(
    *,
    project_store: ProjectStore,
    scheduler: LivePreviewScheduler,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/preview/status")
    def live_preview_status(project_id: str) -> JSONResponse:
        get_project_or_404(project_store, project_id)
        status = LivePreviewStore(
            project_store.project_directory(project_id)
        ).read_status()
        return JSONResponse(
            status.to_dict(), headers={"Cache-Control": "no-store"}
        )

    @router.get("/api/projects/{project_id}/preview")
    def live_preview_artifact(project_id: str) -> FileResponse:
        project = get_project_or_404(project_store, project_id)
        if project.state == ProjectState.DRAFT:
            raise HTTPException(
                status_code=404, detail="Live preview starts with the Agent Run"
            )
        try:
            artifact = LivePreviewStore(
                project_store.project_directory(project_id)
            ).artifact()
        except (OSError, PreviewError) as exc:
            raise HTTPException(
                status_code=404, detail="Live preview is unavailable"
            ) from exc
        return FileResponse(
            artifact,
            media_type="model/gltf-binary",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/api/projects/{project_id}/preview/retry", status_code=202)
    def retry_live_preview(project_id: str) -> JSONResponse:
        project = get_project_or_404(project_store, project_id)
        if project.state != ProjectState.RUNNING:
            raise HTTPException(status_code=409, detail="Live preview is not running")
        try:
            scheduler.retry(project_id)
        except PreviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"accepted": True}, status_code=202)

    @router.post("/api/projects/{project_id}/preview/pause")
    def pause_live_preview(
        project_id: str, request: PreviewPauseRequest
    ) -> JSONResponse:
        project = get_project_or_404(project_store, project_id)
        if project.state != ProjectState.RUNNING:
            raise HTTPException(status_code=409, detail="Live preview is not running")
        try:
            status = scheduler.set_paused(project_id, request.paused)
        except PreviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            status.to_dict(), headers={"Cache-Control": "no-store"}
        )

    return router


__all__ = ["create_preview_router"]
