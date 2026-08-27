"""Validation for browser-facing live GLB previews."""

from __future__ import annotations

import cadflow as cad

from .preview_glb import validate_assembly_preview_glb


MAX_PREVIEW_BYTES = 16 * 1024 * 1024


class PreviewError(ValueError):
    """Raised when a live preview frame is invalid or unsafe."""


def validate_preview_glb(payload: bytes | bytearray | memoryview) -> None:
    """Validate one complete native triangle GLB before exposing it."""

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise PreviewError("preview frame must be binary GLB data")
    if memoryview(payload).nbytes > MAX_PREVIEW_BYTES:
        raise PreviewError("preview frame exceeds the size limit")
    try:
        cad.scene.preflight_glb(payload, expected_kind="triangle")
    except (TypeError, ValueError) as native_error:
        try:
            validate_assembly_preview_glb(bytes(payload))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            raise PreviewError(
                "preview frame is not a valid CadFlow triangle GLB"
            ) from native_error


__all__ = [
    "MAX_PREVIEW_BYTES",
    "PreviewError",
    "validate_preview_glb",
]
