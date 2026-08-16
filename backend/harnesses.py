"""Harness-neutral selection and availability for one-shot Agent Runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


class AgentHarness(str, Enum):
    """Stable machine identifiers accepted by the public Run API."""

    DEEPAGENTS = "deepagents"
    PI = "pi"


HARNESS_LABELS = {
    AgentHarness.DEEPAGENTS: "Deep Agents",
    AgentHarness.PI: "Pi",
}


class HarnessUnavailableError(RuntimeError):
    """Raised before Prompt submission when a selected harness is unavailable."""


@dataclass(frozen=True)
class AgentRunAdapter:
    """Narrow adapter metadata around a harness-specific run service."""

    harness: AgentHarness
    service: Any
    implementation_version: str

    @property
    def label(self) -> str:
        return HARNESS_LABELS[self.harness]

    @property
    def available(self) -> bool:
        value = getattr(self.service, "available", True)
        if callable(value):
            value = value()
        return bool(value)

    @property
    def unavailable_reason(self) -> str | None:
        value = getattr(self.service, "unavailable_reason", None)
        if callable(value):
            value = value()
        return value if isinstance(value, str) and value.strip() else None

    def require_available(self) -> None:
        if self.available:
            return
        reason = self.unavailable_reason or f"{self.label} worker is unavailable"
        raise HarnessUnavailableError(reason)

    def status_payload(self) -> dict[str, object]:
        return {
            "id": self.harness.value,
            "label": self.label,
            "available": self.available,
            "implementation_version": self.implementation_version,
            "unavailable_reason": self.unavailable_reason,
        }


class AgentRunAdapterRegistry:
    """Resolve exactly one adapter for each supported public harness value."""

    def __init__(self, adapters: Iterable[AgentRunAdapter]) -> None:
        self._adapters = {adapter.harness: adapter for adapter in adapters}
        missing = set(AgentHarness) - set(self._adapters)
        if missing:
            values = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"missing Agent Run adapters: {values}")

    def get(self, harness: AgentHarness | str) -> AgentRunAdapter:
        try:
            normalized = AgentHarness(harness)
        except ValueError as exc:
            raise ValueError(f"unsupported Agent harness: {harness}") from exc
        return self._adapters[normalized]

    def statuses(self) -> tuple[dict[str, object], ...]:
        return tuple(self._adapters[item].status_payload() for item in AgentHarness)


__all__ = [
    "AgentHarness",
    "AgentRunAdapter",
    "AgentRunAdapterRegistry",
    "HARNESS_LABELS",
    "HarnessUnavailableError",
]
