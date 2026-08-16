"""Validated live GLB preview files for one CAD execution."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import cadflow as cad


PREVIEW_DIRECTORY_NAME = "previews"
PREVIEW_EXTENSION = ".glb"
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
_OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class PreviewError(ValueError):
    """Raised when a live preview frame is invalid or unsafe."""


@dataclass(frozen=True)
class PreviewFrame:
    """A validated GLB frame published by one CAD execution attempt."""

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
    candidate = attempt_root / f"{revision}{PREVIEW_EXTENSION}"
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
    validate_preview_glb(payload)
    return PreviewFrame(
        attempt=attempt,
        revision=revision,
        operation=operation,
        path=path,
        byte_length=len(payload),
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def validate_preview_glb(payload: bytes | bytearray | memoryview) -> None:
    """Validate one complete native triangle GLB before exposing it."""

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise PreviewError("preview frame must be binary GLB data")
    if memoryview(payload).nbytes > MAX_PREVIEW_BYTES:
        raise PreviewError("preview frame exceeds the size limit")
    try:
        cad.scene.preflight_glb(payload, expected_kind="triangle")
    except (TypeError, ValueError) as exc:
        raise PreviewError("preview frame is not a valid CadFlow triangle GLB") from exc


def _preview_root(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve() / PREVIEW_DIRECTORY_NAME
    if root.is_symlink():
        raise PreviewError("preview directory must not be a symlink")
    return root


def _validate_positive_int(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PreviewError(f"{name} must be a positive integer")


__all__ = [
    "MAX_PREVIEW_BYTES",
    "PREVIEW_DIRECTORY_NAME",
    "PREVIEW_EXTENSION",
    "PreviewError",
    "PreviewFrame",
    "clear_previews",
    "prepare_preview_attempt",
    "preview_path",
    "read_preview_frame",
    "validate_preview_glb",
]
