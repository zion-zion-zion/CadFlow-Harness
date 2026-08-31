"""Same-origin static frontend mounting for the FastAPI application."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI, frontend_dist: str | Path | None) -> None:
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


__all__ = ["mount_frontend"]
