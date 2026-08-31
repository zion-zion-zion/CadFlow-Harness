"""Structured outcomes and errors for bounded Agent Runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..cad_executor import ExecutionResult
from ..cad_review import ReviewResult
from ..contracts import ToolUseRecord
from ..harnesses import AgentHarness
from .settings import DEEPAGENTS_IMPLEMENTATION_VERSION


class AgentRunError(RuntimeError):
    """Raised when a run cannot produce a structured Agent outcome."""


class AgentRunCancelled(AgentRunError):
    """Raised inside a tool when the user has stopped the current Agent Run."""


@dataclass(frozen=True)
class AgentRunOutcome:
    """Safe outcome of one bounded Text-to-CAD Agent Run."""

    validated: bool
    failure_reason: str | None = None
    execution_result: ExecutionResult | None = None
    tool_use_records: tuple[ToolUseRecord, ...] = ()
    execution_results: tuple[ExecutionResult, ...] = ()
    provider_retry_count: int = 0
    duration_seconds: float | None = None
    token_usage: dict[str, int] | None = None
    cancelled: bool = False
    harness: AgentHarness = AgentHarness.DEEPAGENTS
    implementation_version: str | None = DEEPAGENTS_IMPLEMENTATION_VERSION
    review_result: ReviewResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "harness", AgentHarness(self.harness))
        results = self.execution_results
        if not results and self.execution_result is not None:
            object.__setattr__(self, "execution_results", (self.execution_result,))
        elif results and self.execution_result is None:
            object.__setattr__(self, "execution_result", results[-1])

    @property
    def status(self) -> str:
        if self.cancelled:
            return "cancelled"
        return "succeeded" if self.validated else "failed"

    def diagnostics(self) -> dict[str, Any]:
        results = self.execution_results
        return {
            "harness": self.harness.value,
            "implementation_version": self.implementation_version,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "execution_result": (
                self.execution_result.to_dict()
                if self.execution_result is not None
                else None
            ),
            "cad_execution_count": len(results),
            "execution_results": [
                {
                    "attempt": index,
                    **result.to_dict(),
                }
                for index, result in enumerate(results, start=1)
            ],
            "provider_retry_count": self.provider_retry_count,
            "duration_seconds": self.duration_seconds,
            "token_usage": self.token_usage,
            "cancelled": self.cancelled,
            "tool_use_records": [
                {
                    "sequence": record.sequence,
                    "tool_name": record.tool_name,
                    "target": record.target,
                    "reference_names": list(record.reference_names),
                }
                for record in self.tool_use_records
            ],
            "review_result": (
                self.review_result.to_dict() if self.review_result is not None else None
            ),
        }


def is_validated_result(result: ExecutionResult) -> bool:
    """Return whether an execution result is a deterministically validated product."""

    return result.is_validated_product


__all__ = [
    "AgentRunError",
    "AgentRunCancelled",
    "AgentRunOutcome",
    "is_validated_result",
]
