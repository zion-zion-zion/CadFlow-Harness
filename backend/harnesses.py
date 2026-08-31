"""Persisted Agent harness metadata retained for API compatibility."""

from __future__ import annotations

from enum import Enum


class AgentHarness(str, Enum):
    """Stable machine identifier accepted by the public Run API."""

    DEEPAGENTS = "deepagents"


__all__ = ["AgentHarness"]
