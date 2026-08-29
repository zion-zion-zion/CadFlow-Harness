"""Deterministic, repair-oriented summaries of CAD and review failures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Mapping

from .cad_executor import ExecutionResult
from .cad_review import ReviewResult


class FailureType(str, Enum):
    """Stable top-level routes for progressive CAD repair."""

    EXECUTION_ERROR = "EXECUTION_ERROR"
    GEOMETRY_ERROR = "GEOMETRY_ERROR"
    ASSEMBLY_ERROR = "ASSEMBLY_ERROR"
    REQUIREMENT_REVIEW_ERROR = "REQUIREMENT_REVIEW_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"


@dataclass(frozen=True)
class FailurePacket:
    """A bounded diagnosis that complements, but never replaces, raw results."""

    primary_type: FailureType
    failure_signature: str
    summary: str
    key_evidence: tuple[str, ...]
    source_scope: tuple[str, ...]
    preserve_conditions: tuple[str, ...]
    suggested_action: str
    source_edit_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_type": self.primary_type.value,
            "failure_signature": self.failure_signature,
            "summary": self.summary,
            "key_evidence": list(self.key_evidence),
            "source_scope": list(self.source_scope),
            "preserve_conditions": list(self.preserve_conditions),
            "suggested_action": self.suggested_action,
            "source_edit_allowed": self.source_edit_allowed,
        }


_ASSEMBLY_CHECKS = frozenset(
    {
        "strict_constraint_solve",
        "constraint_residuals",
        "current_pose_collision",
    }
)
_INFRASTRUCTURE_ERROR_TYPES = frozenset(
    {"cancelled", "infrastructure", "product_artifact", "review_evidence", "scene"}
)
_GEOMETRY_ERROR_TYPES = frozenset({"geometry", "topology"})


def normalize_execution_failure(result: ExecutionResult) -> FailurePacket | None:
    """Normalize one executor result without discarding its raw diagnostics."""

    if result.is_validated_product:
        return None
    checks = tuple(result.product_validation_checks)
    failed_checks = tuple(
        check for check in checks if str(check.get("status", "")).lower() == "failed"
    )
    failed_ids = tuple(
        str(check.get("check_id"))
        for check in failed_checks
        if isinstance(check.get("check_id"), str)
    )
    passed_ids = tuple(
        str(check.get("check_id"))
        for check in checks
        if str(check.get("status", "")).lower() == "passed"
        and isinstance(check.get("check_id"), str)
    )
    error_type = (result.error_type or "execution_boundary").strip().lower()
    if error_type in _INFRASTRUCTURE_ERROR_TYPES:
        primary_type = FailureType.INFRASTRUCTURE_ERROR
    elif result.result_kind == "assembly" and (
        error_type == "product_validation"
        or any(check_id in _ASSEMBLY_CHECKS for check_id in failed_ids)
    ):
        primary_type = FailureType.ASSEMBLY_ERROR
    elif error_type in _GEOMETRY_ERROR_TYPES or error_type == "product_validation":
        primary_type = FailureType.GEOMETRY_ERROR
    else:
        primary_type = FailureType.EXECUTION_ERROR

    evidence = _execution_evidence(result, failed_checks)
    signature_payload = {
        "primary_type": primary_type.value,
        "error_type": error_type,
        "failed_checks": [
            {
                "check_id": check.get("check_id"),
                "message": _stable_text(check.get("message")),
            }
            for check in failed_checks
        ],
        "error": _stable_text(result.error) if not failed_checks else None,
        "source": _source_location(result.error_location),
    }
    source_edit_allowed = primary_type is not FailureType.INFRASTRUCTURE_ERROR
    source_scope = (
        _source_scope(result.error_location) if source_edit_allowed else ()
    )
    return FailurePacket(
        primary_type=primary_type,
        failure_signature=_signature(primary_type, signature_payload),
        summary=_bounded_text(
            result.error
            or next(
                (
                    str(check.get("message"))
                    for check in failed_checks
                    if check.get("message")
                ),
                "CAD validation did not produce a passing product.",
            )
        ),
        key_evidence=evidence,
        source_scope=source_scope,
        preserve_conditions=tuple(dict.fromkeys(passed_ids)),
        suggested_action=_execution_action(primary_type, failed_ids),
        source_edit_allowed=source_edit_allowed,
    )


def normalize_review_failure(result: ReviewResult) -> FailurePacket | None:
    """Normalize one independent CAD Review result."""

    if result.status == "pass":
        return None
    blocking_findings = tuple(
        finding
        for finding in result.findings
        if finding.severity in {"blocking", "major"}
    )
    infrastructure = bool(blocking_findings) and all(
        finding.category == "review_infrastructure"
        for finding in blocking_findings
    )
    primary_type = (
        FailureType.INFRASTRUCTURE_ERROR
        if infrastructure
        else FailureType.REQUIREMENT_REVIEW_ERROR
    )
    signature_payload = {
        "primary_type": primary_type.value,
        "findings": [
            {
                "category": finding.category,
                "severity": finding.severity,
                "requirement": _stable_text(finding.requirement),
            }
            for finding in result.findings
            if finding.severity in {"blocking", "major"}
        ],
        "summary": _stable_text(result.summary) if not result.findings else None,
    }
    source_edit_allowed = primary_type is not FailureType.INFRASTRUCTURE_ERROR
    evidence = tuple(
        _bounded_text(
            f"{finding.category}: {finding.requirement} Observed: {finding.observed}"
        )
        for finding in result.findings[:8]
    ) or (_bounded_text(result.summary or "CAD Review failed."),)
    return FailurePacket(
        primary_type=primary_type,
        failure_signature=_signature(primary_type, signature_payload),
        summary=_bounded_text(result.summary or "CAD Review failed."),
        key_evidence=evidence,
        source_scope=("code/model.py",) if source_edit_allowed else (),
        preserve_conditions=tuple(dict.fromkeys(result.checked_requirements)),
        suggested_action=(
            "Address the blocking requirement findings, preserve already checked "
            "requirements, then validate the changed source before review."
            if source_edit_allowed
            else "Do not modify CAD source; retry cad_review or repair the host review evidence."
        ),
        source_edit_allowed=source_edit_allowed,
    )


def _execution_evidence(
    result: ExecutionResult,
    failed_checks: tuple[Mapping[str, object], ...],
) -> tuple[str, ...]:
    evidence: list[str] = []
    if result.error_type:
        evidence.append(f"error_type={result.error_type}")
    if result.error_location:
        evidence.append(f"location={_source_location(result.error_location)}")
    for check in failed_checks[:8]:
        check_id = check.get("check_id")
        message = check.get("message")
        evidence.append(
            _bounded_text(
                f"{check_id}: {message}"
                if message
                else f"validation check failed: {check_id}"
            )
        )
    if not failed_checks and result.error:
        evidence.append(_bounded_text(result.error))
    return tuple(dict.fromkeys(evidence))


def _execution_action(primary_type: FailureType, failed_ids: tuple[str, ...]) -> str:
    if primary_type is FailureType.INFRASTRUCTURE_ERROR:
        return (
            "Do not modify CAD source; retry the failed host operation or repair "
            "its execution, artifact, Scene, or review-evidence infrastructure."
        )
    if primary_type is FailureType.ASSEMBLY_ERROR:
        detail = ", ".join(failed_ids) or "the failed Assembly validation check"
        return f"Repair {detail} while preserving every passed Assembly check."
    if primary_type is FailureType.GEOMETRY_ERROR:
        return (
            "Repair the reported solid topology or product-validation condition "
            "within the likely source scope, preserving passed checks."
        )
    return (
        "Repair the reported Python, import, public-API, runtime, or timeout cause "
        "at the indicated source scope before validating again."
    )


def _signature(primary_type: FailureType, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"{primary_type.value}:{hashlib.sha256(encoded).hexdigest()[:20]}"


def _source_scope(location: str | None) -> tuple[str, ...]:
    normalized = _source_location(location)
    if not normalized:
        return ("code/model.py",)
    path = normalized.split(":", 1)[0]
    return (path if path.startswith("code/") else f"code/{path}",)


def _source_location(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("\\", "/")
    match = re.search(r"(?:^|/)(code/[^:\s]+\.py)(?::(\d+))?", text)
    if match:
        return match.group(1)
    filename = PurePosixPath(text.split(":", 1)[0]).name
    return filename or None


def _stable_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().lower().replace("\\", "/")
    text = re.sub(r"(?:[a-z]:)?/[^\s:]+/(code/[^\s:]+)", r"\1", text)
    text = re.sub(r"\b0x[0-9a-f]+\b", "<address>", text)
    text = re.sub(r"\b(?:pid|process)[ =:]?\d+\b", "pid=<n>", text)
    text = re.sub(r"(?<![a-z_])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", "<n>", text)
    text = re.sub(r"\s+", " ", text)
    return text[:1_000]


def _bounded_text(value: str, limit: int = 2_000) -> str:
    text = " ".join(value.split())
    return text[:limit]


__all__ = [
    "FailurePacket",
    "FailureType",
    "normalize_execution_failure",
    "normalize_review_failure",
]
