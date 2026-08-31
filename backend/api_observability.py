"""Progress Event, SSE, and redacted trace HTTP routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .api_payloads import get_project_or_404, project_payload
from .events import ProgressEventStore
from .projects import ProjectStore
from .trace import (
    TraceError,
    iter_redacted_trace,
    read_trace,
    read_trace_event,
    trace_stats,
)


KEEPALIVE_SECONDS = 15.0


def create_observability_router(
    *,
    project_store: ProjectStore,
    event_store: ProgressEventStore,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/traces")
    def list_traces() -> JSONResponse:
        payload = []
        for project in project_store.list_projects():
            item = project_payload(project_store, project)
            project_dir = project_store.project_directory(project.project_id)
            item.update(trace_stats(project_dir))
            payload.append(item)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/api/projects/{project_id}/events")
    async def project_events(
        request: Request,
        project_id: str,
        last_event_id_header: str | None = Header(
            default=None, alias="Last-Event-ID"
        ),
        last_event_id: int | None = Query(default=None, ge=0),
        follow: bool = Query(default=True),
    ) -> StreamingResponse:
        get_project_or_404(project_store, project_id)
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

    @router.get("/api/projects/{project_id}/trace")
    def project_trace(
        project_id: str,
        offset: int = Query(default=0, ge=0),
        q: str = Query(default="", max_length=500),
    ) -> JSONResponse:
        project = get_project_or_404(project_store, project_id)
        project_dir = project_store.project_directory(project_id)
        try:
            batch = read_trace(project_dir, offset=offset, query=q)
        except TraceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = batch.to_dict()
        payload["project"] = project_payload(project_store, project)
        payload["trace"] = trace_stats(project_dir)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    @router.get("/api/projects/{project_id}/trace/download")
    def download_project_trace(project_id: str) -> StreamingResponse:
        get_project_or_404(project_store, project_id)
        try:
            content = iter_redacted_trace(
                project_store.project_directory(project_id)
            )
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

    @router.get("/api/projects/{project_id}/trace/events")
    def project_trace_event(
        project_id: str,
        cursor: int = Query(ge=0),
    ) -> JSONResponse:
        get_project_or_404(project_store, project_id)
        try:
            payload = read_trace_event(
                project_store.project_directory(project_id), cursor
            )
        except TraceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    return router


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


__all__ = ["KEEPALIVE_SECONDS", "create_observability_router"]
