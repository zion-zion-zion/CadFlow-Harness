"""Errors shared by the browser-facing live preview boundary."""

from __future__ import annotations


class PreviewError(ValueError):
    """Raised when a live preview artifact is invalid or unavailable."""


__all__ = ["PreviewError"]
