"""Validated live mesh preview files for one CAD execution."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PREVIEW_DIRECTORY_NAME = "previews"
PREVIEW_SCHEMA_VERSION = 1
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_COORDINATES = 3_000_000
MAX_PREVIEW_INDICES = 3_000_000
_OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class PreviewError(ValueError):
    """Raised when a live preview frame is invalid or unsafe."""


@dataclass(frozen=True)
class PreviewFrame:
    """A validated mesh frame published by one CAD execution attempt."""

    attempt: int
    revision: int
    operation: str
    path: Path
    byte_length: int
    content_hash: str


def prepare_preview_attempt(project_dir: str | Path, attempt: int) -> Path:
    """Create an empty attempt directory without touching other attempts."""

    root = _preview_root(project_dir)
    _validate_positive_int(attempt, "attempt")
    root.mkdir(parents=True, exist_ok=True)
    path = root / str(attempt)
    if path.is_symlink():
        path.unlink()
    elif path.exists() and not path.is_dir():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    path.mkdir()
    return path


def preview_path(
    project_dir: str | Path,
    attempt: int,
    revision: int,
) -> Path:
    """Resolve one preview path after validating its opaque numeric IDs."""

    _validate_positive_int(attempt, "attempt")
    _validate_positive_int(revision, "revision")
    root = _preview_root(project_dir)
    attempt_root = root / str(attempt)
    if attempt_root.is_symlink():
        raise PreviewError("preview attempt directory must not be a symlink")
    candidate = attempt_root / f"{revision}.json"
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PreviewError("preview is outside the Project") from exc
    return candidate


def clear_previews(project_dir: str | Path) -> None:
    """Remove all live preview frames for a Project."""

    root = _preview_root(project_dir)
    if root.is_symlink():
        root.unlink()
    elif root.is_dir():
        shutil.rmtree(root)


def read_preview_frame(
    project_dir: str | Path,
    *,
    attempt: int,
    revision: int,
    operation: str,
) -> PreviewFrame:
    """Validate a frame written by the CAD child before exposing it."""

    if not _OPERATION_PATTERN.fullmatch(operation):
        raise PreviewError("preview operation is invalid")
    path = preview_path(project_dir, attempt, revision)
    if path.is_symlink() or not path.is_file():
        raise PreviewError("preview frame is missing")
    payload = path.read_bytes()
    if len(payload) > MAX_PREVIEW_BYTES:
        raise PreviewError("preview frame exceeds the size limit")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreviewError("preview frame is not valid JSON") from exc
    _validate_mesh_document(document, operation)
    return PreviewFrame(
        attempt=attempt,
        revision=revision,
        operation=operation,
        path=path,
        byte_length=len(payload),
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def validate_mesh_document(document: Any, operation: str | None = None) -> None:
    """Validate a decoded preview document for tests and future transports."""

    _validate_mesh_document(document, operation)


def _preview_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve() / PREVIEW_DIRECTORY_NAME
    if root.is_symlink():
        raise PreviewError("preview directory must not be a symlink")
    return root


def _validate_mesh_document(document: Any, operation: str | None) -> None:
    if not isinstance(document, dict):
        raise PreviewError("preview frame must be an object")
    if document.get("schema_version") != PREVIEW_SCHEMA_VERSION:
        raise PreviewError("unsupported preview schema")
    actual_operation = document.get("operation")
    if not isinstance(actual_operation, str) or not _OPERATION_PATTERN.fullmatch(actual_operation):
        raise PreviewError("preview operation is invalid")
    if operation is not None and actual_operation != operation:
        raise PreviewError("preview operation does not match its event")
    vertices = document.get("vertices")
    triangles = document.get("triangles")
    if not isinstance(vertices, list) or not isinstance(triangles, list):
        raise PreviewError("preview mesh must contain vertices and triangles")
    if len(vertices) == 0 or len(vertices) % 3 != 0:
        raise PreviewError("preview vertices must contain complete 3D coordinates")
    if len(triangles) == 0 or len(triangles) % 3 != 0:
        raise PreviewError("preview triangles must contain complete faces")
    if len(vertices) > MAX_PREVIEW_COORDINATES:
        raise PreviewError("preview vertex count exceeds the size limit")
    if len(triangles) > MAX_PREVIEW_INDICES:
        raise PreviewError("preview index count exceeds the size limit")
    vertex_count = len(vertices) // 3
    for value in vertices:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise PreviewError("preview vertices must be finite numbers")
    for value in triangles:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value >= vertex_count:
            raise PreviewError("preview triangle index is invalid")


def _validate_positive_int(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PreviewError(f"{name} must be a positive integer")


__all__ = [
    "MAX_PREVIEW_BYTES",
    "MAX_PREVIEW_COORDINATES",
    "MAX_PREVIEW_INDICES",
    "PREVIEW_DIRECTORY_NAME",
    "PREVIEW_SCHEMA_VERSION",
    "PreviewError",
    "PreviewFrame",
    "clear_previews",
    "prepare_preview_attempt",
    "preview_path",
    "read_preview_frame",
    "validate_mesh_document",
]
