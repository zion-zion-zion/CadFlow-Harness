"""Scene and accepted product artifact HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from .api_payloads import (
    get_project_or_404,
    product_artifact_or_404,
    product_media_type,
    product_payload,
)
from .product_artifact import ProductArtifactError
from .projects import ProjectStateError, ProjectStore


def create_artifact_router(*, project_store: ProjectStore) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/scene")
    def project_scene(project_id: str) -> FileResponse:
        get_project_or_404(project_store, project_id)
        try:
            artifact = project_store.scene_artifact(project_id)
        except ProjectStateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            artifact,
            media_type="application/zip",
            filename="model.scene.zip",
        )

    @router.get("/api/projects/{project_id}/product")
    def project_product(project_id: str) -> JSONResponse:
        artifact = product_artifact_or_404(project_store, project_id)
        return JSONResponse(
            product_payload(project_id, artifact),
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/projects/{project_id}/product/manifest")
    def project_product_manifest(project_id: str) -> FileResponse:
        artifact = product_artifact_or_404(project_store, project_id)
        return FileResponse(
            artifact.root / "product.json",
            media_type="application/json",
            filename="product.json",
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/projects/{project_id}/product/files/{role}")
    def project_product_file(project_id: str, role: str) -> FileResponse:
        artifact = product_artifact_or_404(project_store, project_id)
        try:
            path = artifact.file_path(role)
        except ProductArtifactError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=product_media_type(role),
            filename=path.name,
            headers={"Cache-Control": "no-store"},
        )

    @router.get("/api/projects/{project_id}/product/part-step")
    def project_part_step(
        project_id: str,
        part_id: str = Query(min_length=1, max_length=512),
    ) -> FileResponse:
        artifact = product_artifact_or_404(project_store, project_id)
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

    return router


__all__ = ["create_artifact_router"]
