"""HTTP projections for Projects and accepted product artifacts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException

from .live_preview import LivePreviewStore
from .product_artifact import ProductArtifact
from .projects import (
    Project,
    ProjectNotFoundError,
    ProjectStateError,
    ProjectStore,
)


def get_project_or_404(store: ProjectStore, project_id: str) -> Project:
    try:
        return store.get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def project_payload(store: ProjectStore, project: Project) -> dict[str, object]:
    scene_is_available = scene_available(store, project)
    diagnostics = store.read_diagnostics(project.project_id)
    preview = LivePreviewStore(
        store.project_directory(project.project_id)
    ).read_status()
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
        "scene_available": scene_is_available,
        "artifact_version": artifact_version,
        "product_available": product_available,
        "result_kind": result_kind,
        "product_status": "Accepted" if product_available else None,
        "turn_count": len(store.conversation_turns(project.project_id)),
        "preview": preview.to_dict(),
        "diagnostics_available": diagnostics is not None,
        "duration_seconds": (
            duration_seconds(diagnostics) if metrics_available else None
        ),
        "token_usage": token_usage(diagnostics) if metrics_available else None,
    }


def product_artifact_or_404(
    store: ProjectStore, project_id: str
) -> ProductArtifact:
    get_project_or_404(store, project_id)
    try:
        return store.product_artifact(project_id)
    except ProjectStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def product_payload(project_id: str, artifact: ProductArtifact) -> dict[str, Any]:
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


def product_media_type(role: str) -> str:
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


def scene_available(store: ProjectStore, project: Project) -> bool:
    try:
        return store.scene_artifact(project.project_id).is_file()
    except ProjectStateError:
        return False


def duration_seconds(diagnostics: Mapping[str, Any] | None) -> float | None:
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


def token_usage(
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


__all__ = [
    "duration_seconds",
    "get_project_or_404",
    "product_artifact_or_404",
    "product_media_type",
    "product_payload",
    "project_payload",
    "scene_available",
    "token_usage",
]
