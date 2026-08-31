"""FastAPI boundary for the observable, cancellable local Agent Run."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import (
    AgentRunService,
    AgentSettings,
    ReferenceGroundedAgent,
)
from .events import ProgressEventStore
from .harnesses import AgentHarness
from .live_preview import LivePreviewScheduler, LivePreviewStore
from .previews import PreviewError
from .product_artifact import ProductArtifact, ProductArtifactError
from .projects import (
    ProjectError,
    ProjectNotFoundError,
    ProjectState,
    ProjectStateError,
    ProjectStore,
    PromptValidationError,
)
from .run_coordinator import AgentRunCoordinator, RunConflictError
from .trace import (
    TraceError,
    iter_redacted_trace,
    read_trace,
    read_trace_event,
    trace_stats,
)


KEEPALIVE_SECONDS = 15.0
DEFAULT_PROJECTS_ROOT = Path(
    os.environ.get("TEXT_TO_CAD_PROJECTS_ROOT", "output/projects")
)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)


class RunProjectRequest(BaseModel):
    prompt: str
    harness: AgentHarness = AgentHarness.DEEPAGENTS


class ProjectMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    request_id: str = Field(min_length=1, max_length=128)
    retry_of: str | None = Field(default=None, min_length=1, max_length=128)
    harness: AgentHarness = AgentHarness.DEEPAGENTS


class DeleteProjectRequest(BaseModel):
    """Accept the UI's name confirmation without making it a Project ID."""

    name: str | None = Field(default=None, min_length=1)
    confirmation: str | None = Field(default=None, min_length=1)
    confirm_name: str | None = Field(default=None, min_length=1)

    @property
    def value(self) -> str | None:
        return self.confirmation or self.confirm_name or self.name


class PreviewPauseRequest(BaseModel):
    paused: bool


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
            preview_attempt=(1 if status.state == "current" and status.revision > 0 else None),
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

    app = FastAPI(title="CadFlowAgent", lifespan=lifespan)
    app.state.project_store = project_store
    app.state.event_store = event_store
    app.state.run_coordinator = coordinator
    app.state.preview_scheduler = scheduler

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, object]]:
        return [
            _project_payload(project_store, project)
            for project in project_store.list_projects()
        ]

    @app.get("/api/traces")
    def list_traces() -> JSONResponse:
        payload = []
        for project in project_store.list_projects():
            item = _project_payload(project_store, project)
            project_dir = project_store.project_directory(project.project_id)
            item.update(trace_stats(project_dir))
            payload.append(item)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

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
            project = coordinator.start(project_id, request.prompt, request.harness)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PromptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _project_payload(project_store, project)

    @app.get("/api/projects/{project_id}/messages")
    def project_messages(project_id: str) -> JSONResponse:
        _get_project_or_404(project_store, project_id)
        return JSONResponse(
            {
                "conversation_id": project_id,
                "turns": project_store.conversation_turns(project_id),
                "current_artifact_version": project_store.current_artifact_version(
                    project_id
                ),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/projects/{project_id}/messages")
    def create_project_message(
        project_id: str,
        request: ProjectMessageRequest,
    ) -> dict[str, object]:
        try:
            submission = coordinator.start_message(
                project_id,
                request.message,
                request_id=request.request_id,
                retry_of=request.retry_of,
                harness=request.harness,
            )
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RunConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PromptValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        coordinator.wait_for_turn(submission.turn_id)
        project = project_store.get_project(project_id)
        turn = project_store.conversation_log(project_id).turn(submission.turn_id)
        if turn is None:
            raise HTTPException(status_code=500, detail="Conversation turn was not persisted")
        return {
            "turn": turn,
            "project": _project_payload(project_store, project),
            "artifact": {
                "version": project_store.current_artifact_version(project_id),
                "scene_available": _scene_available(project_store, project),
            },
            "duplicate": submission.duplicate,
        }

    @app.delete("/api/projects/{project_id}/conversation")
    def clear_project_conversation(
        project_id: str,
        request: DeleteProjectRequest,
    ) -> dict[str, object]:
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
            reset = project_store.clear_conversation(project_id)
        except ProjectStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _project_payload(project_store, reset)

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

    @app.get("/api/projects/{project_id}/product")
    def project_product(project_id: str) -> JSONResponse:
        artifact = _product_artifact_or_404(project_store, project_id)
        return JSONResponse(
            _product_payload(project_id, artifact),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/projects/{project_id}/product/manifest")
    def project_product_manifest(project_id: str) -> FileResponse:
        artifact = _product_artifact_or_404(project_store, project_id)
        return FileResponse(
            artifact.root / "product.json",
            media_type="application/json",
            filename="product.json",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/projects/{project_id}/product/files/{role}")
    def project_product_file(project_id: str, role: str) -> FileResponse:
        artifact = _product_artifact_or_404(project_store, project_id)
        try:
            path = artifact.file_path(role)
        except ProductArtifactError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=_product_media_type(role),
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/projects/{project_id}/product/part-step")
    def project_part_step(
        project_id: str,
        part_id: str = Query(min_length=1, max_length=512),
    ) -> FileResponse:
        artifact = _product_artifact_or_404(project_store, project_id)
        try:
            path = artifact.part_file_path(part_id)
        except ProductArtifactError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="model/step",
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/projects/{project_id}/trace")
    def project_trace(
        project_id: str,
        offset: int = Query(default=0, ge=0),
        q: str = Query(default="", max_length=500),
    ) -> JSONResponse:
        project = _get_project_or_404(project_store, project_id)
        project_dir = project_store.project_directory(project_id)
        try:
            batch = read_trace(project_dir, offset=offset, query=q)
        except TraceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = batch.to_dict()
        payload["project"] = _project_payload(project_store, project)
        payload["trace"] = trace_stats(project_dir)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/trace/download")
    def download_project_trace(project_id: str) -> StreamingResponse:
        _get_project_or_404(project_store, project_id)
        try:
            content = iter_redacted_trace(project_store.project_directory(project_id))
        except TraceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return StreamingResponse(
            content,
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": (
                    f'attachment; filename="{project_id}-conversation.redacted.jsonl"'
                ),
            },
        )

    @app.get("/api/projects/{project_id}/trace/events")
    def project_trace_event(
        project_id: str,
        cursor: int = Query(ge=0),
    ) -> JSONResponse:
        _get_project_or_404(project_store, project_id)
        try:
            payload = read_trace_event(
                project_store.project_directory(project_id), cursor
            )
        except TraceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/preview/status")
    def live_preview_status(project_id: str) -> JSONResponse:
        _get_project_or_404(project_store, project_id)
        status = LivePreviewStore(
            project_store.project_directory(project_id)
        ).read_status()
        return JSONResponse(status.to_dict(), headers={"Cache-Control": "no-store"})

    @app.get("/api/projects/{project_id}/preview")
    def live_preview_artifact(project_id: str) -> FileResponse:
        project = _get_project_or_404(project_store, project_id)
        if project.state == ProjectState.DRAFT:
            raise HTTPException(
                status_code=404, detail="Live preview starts with the Agent Run"
            )
        try:
            artifact = LivePreviewStore(
                project_store.project_directory(project_id)
            ).artifact()
        except (OSError, PreviewError) as exc:
            raise HTTPException(status_code=404, detail="Live preview is unavailable") from exc
        return FileResponse(
            artifact,
            media_type="model/gltf-binary",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/projects/{project_id}/preview/retry", status_code=202)
    def retry_live_preview(project_id: str) -> JSONResponse:
        project = _get_project_or_404(project_store, project_id)
        if project.state != ProjectState.RUNNING:
            raise HTTPException(status_code=409, detail="Live preview is not running")
        try:
            scheduler.retry(project_id)
        except PreviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"accepted": True}, status_code=202)

    @app.post("/api/projects/{project_id}/preview/pause")
    def pause_live_preview(
        project_id: str, request: PreviewPauseRequest
    ) -> JSONResponse:
        project = _get_project_or_404(project_store, project_id)
        if project.state != ProjectState.RUNNING:
            raise HTTPException(status_code=409, detail="Live preview is not running")
        try:
            status = scheduler.set_paused(project_id, request.paused)
        except PreviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(status.to_dict(), headers={"Cache-Control": "no-store"})

    _mount_frontend(app, frontend_dist)
    return app


def _get_project_or_404(store: ProjectStore, project_id: str):
    try:
        return store.get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _project_payload(store: ProjectStore, project: Any) -> dict[str, object]:
    scene_available = _scene_available(store, project)
    diagnostics = store.read_diagnostics(project.project_id)
    preview = LivePreviewStore(store.project_directory(project.project_id)).read_status()
    metrics_available = project.state.value in {"Succeeded", "Failed", "Stopped"}
    artifact_version = store.current_artifact_version(project.project_id)
    result_kind = store.current_result_kind(project.project_id)
    product_available = artifact_version is not None and result_kind is not None
    return {
        "project_id": project.project_id,
        "name": project.name,
        "state": project.state.value,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "prompt": project.prompt,
        "failure_reason": project.failure_reason,
        "harness": project.harness.value,
        "scene_available": scene_available,
        "artifact_version": artifact_version,
        "product_available": product_available,
        "result_kind": result_kind,
        "product_status": "Accepted" if product_available else None,
        "turn_count": len(store.conversation_turns(project.project_id)),
        "preview": preview.to_dict(),
        "diagnostics_available": diagnostics is not None,
        "duration_seconds": (
            _duration_seconds(diagnostics) if metrics_available else None
        ),
        "token_usage": _token_usage(diagnostics) if metrics_available else None,
    }


def _product_artifact_or_404(store: ProjectStore, project_id: str) -> ProductArtifact:
    _get_project_or_404(store, project_id)
    try:
        return store.product_artifact(project_id)
    except ProjectStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _product_payload(project_id: str, artifact: ProductArtifact) -> dict[str, Any]:
    summary = artifact.summary
    files = {
        role: {
            "path": record.relative_path,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "download_url": f"/api/projects/{project_id}/product/files/{quote(role)}",
        }
        for role, record in artifact.files.items()
    }
    return {
        "schema_version": "cadflow-product-api/v1",
        "result_kind": artifact.result_kind,
        "status": artifact.status.value,
        "manifest_url": f"/api/projects/{project_id}/product/manifest",
        "summary": {
            "component_count": summary.component_count,
            "leaf_part_count": summary.leaf_part_count,
            "unique_part_count": summary.unique_part_count,
            "solid_count": summary.solid_count,
            "volume_mm3": summary.volume_mm3,
        },
        "files": files,
        "parts": [
            {
                "part_id": part.part_id,
                "quantity": part.quantity,
                "component_paths": list(part.component_paths),
                "step_path": part.file.relative_path,
                "sha256": part.file.sha256,
                "size_bytes": part.file.size_bytes,
                "download_url": (
                    f"/api/projects/{project_id}/product/part-step?part_id="
                    + quote(part.part_id, safe="")
                ),
            }
            for part in artifact.parts
        ],
        "semantic_model": (
            dict(artifact.semantic_model)
            if artifact.semantic_model is not None
            else None
        ),
        "bom": [
            {
                "part_id": item.part_id,
                "name": item.name,
                "material": item.material,
                "quantity": item.quantity,
                "component_paths": list(item.component_paths),
                "step_path": item.step_path,
            }
            for item in artifact.bom
        ],
        "assumptions": list(artifact.assumptions),
        "validation_report": (
            dict(artifact.validation_report)
            if artifact.validation_report is not None
            else None
        ),
    }


def _product_media_type(role: str) -> str:
    if role == "product_step":
        return "model/step"
    if role in {"scene", "source_snapshot"}:
        return "application/zip"
    if role in {
        "assumptions",
        "bom",
        "semantic_model",
        "validation_report",
    }:
        return "application/json"
    return "application/octet-stream"


def _scene_available(store: ProjectStore, project: Any) -> bool:
    try:
        return store.scene_artifact(project.project_id).is_file()
    except ProjectStateError:
        return False


def _duration_seconds(diagnostics: Mapping[str, Any] | None) -> float | None:
    if diagnostics is None:
        return None
    value = diagnostics.get("duration_seconds")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return float(value)
    return None


def _token_usage(
    diagnostics: Mapping[str, Any] | None,
) -> dict[str, int] | None:
    if diagnostics is None:
        return None
    raw_usage = diagnostics.get("token_usage")
    if not isinstance(raw_usage, Mapping):
        return None
    input_tokens = _non_negative_token_count(raw_usage.get("input_tokens"))
    output_tokens = _non_negative_token_count(raw_usage.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    cached_input_tokens = _non_negative_token_count(
        raw_usage.get("cached_input_tokens")
    )
    cached_input_tokens = min(cached_input_tokens or 0, input_tokens)
    return {
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": input_tokens - cached_input_tokens,
        "output_tokens": output_tokens,
    }


def _non_negative_token_count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


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

    trace_index = candidate / "trace.html"

    @app.get("/", include_in_schema=False)
    def frontend_index() -> FileResponse:
        return FileResponse(index, media_type="text/html")

    if trace_index.is_file():

        @app.get("/trace", include_in_schema=False)
        @app.get("/trace/{project_id}", include_in_schema=False)
        def trace_frontend(project_id: str | None = None) -> FileResponse:
            del project_id
            return FileResponse(trace_index, media_type="text/html")

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
