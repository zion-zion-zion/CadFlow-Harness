"""Stable, bounded contract returned by one CAD execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from .scene_validation import SceneParseResult


_VALIDATION_CHECK_LIMIT = 16
_VALIDATION_MAPPING_LIMIT = 32
_VALIDATION_TEXT_LIMIT = 1024
_VALIDATION_DEPTH_LIMIT = 8
_VALIDATION_NODE_LIMIT = 768
_VALIDATION_SEQUENCE_LIMIT = 32
_VALIDATION_SEQUENCE_LIMITS = {
    "failures": 12,
    "residuals": 24,
    "warnings": 8,
    "grounded_component_ids": 64,
    "solved_component_ids": 64,
    "unsolved_component_ids": 64,
}


@dataclass(frozen=True)
class ExecutionResult:
    """Observable facts from one bounded CAD execution."""

    status: str
    exit_code: int | None
    error: str | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    final_shape_count: int | None
    solid_count: int | None
    solid_volume: float | None
    scene_artifact_exists: bool
    scene_parse_result: SceneParseResult
    artifact_entries: tuple[str, ...]
    duration_seconds: float
    process_id: int | None = None
    error_type: str | None = None
    error_location: str | None = None
    preflight_status: str = "not_run"
    imported_modules: tuple[str, ...] = ()
    review_artifact_dir: str | None = None
    review_manifest_path: str | None = None
    review_model_sha256: str | None = None
    review_evidence_error: str | None = None
    result_kind: str | None = None
    component_count: int | None = None
    leaf_part_count: int | None = None
    unique_part_count: int | None = None
    product_manifest_path: str | None = None
    product_status: str | None = None
    product_validation_status: str | None = None
    product_validation_failures: tuple[str, ...] = ()
    product_validation_checks: tuple[dict[str, object], ...] = ()
    execution_phase: str | None = None
    validation_short_circuited: bool = False

    @property
    def output_truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated

    @property
    def is_validated_product(self) -> bool:
        """Return whether this result may proceed to independent CAD review."""

        if self.result_kind == "part":
            structure_is_valid = bool(
                self.component_count == 0
                and self.leaf_part_count == 1
                and self.unique_part_count == 1
                and self.solid_count == 1
            )
        elif self.result_kind == "assembly":
            structure_is_valid = bool(
                self.component_count is not None
                and self.leaf_part_count is not None
                and self.unique_part_count is not None
                and self.component_count >= self.leaf_part_count >= 1
                and 1 <= self.unique_part_count <= self.leaf_part_count
                and self.solid_count == self.leaf_part_count
            )
        else:
            structure_is_valid = False
        required_entries = {"model.scene.zip", "product.json", "validation.json"}
        return bool(
            self.status == "succeeded"
            and self.exit_code == 0
            and self.final_shape_count == 1
            and structure_is_valid
            and self.solid_volume is not None
            and math.isfinite(self.solid_volume)
            and self.solid_volume > 0
            and self.scene_artifact_exists
            and self.scene_parse_result.valid
            and required_entries.issubset(self.artifact_entries)
            and self.product_manifest_path == "artifacts/product.json"
            and self.product_status == "Draft"
            and self.product_validation_status == "Passed"
            and not self.product_validation_failures
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "final_shape_count": self.final_shape_count,
            "solid_count": self.solid_count,
            "solid_volume": self.solid_volume,
            "scene_artifact_exists": self.scene_artifact_exists,
            "scene_parse_result": self.scene_parse_result.to_dict(),
            "artifact_entries": list(self.artifact_entries),
            "duration_seconds": self.duration_seconds,
            "process_id": self.process_id,
            "error_type": self.error_type,
            "error_location": self.error_location,
            "preflight_status": self.preflight_status,
            "imported_modules": list(self.imported_modules),
            "review_artifact_dir": self.review_artifact_dir,
            "review_manifest_path": self.review_manifest_path,
            "review_model_sha256": self.review_model_sha256,
            "review_evidence_error": self.review_evidence_error,
            "result_kind": self.result_kind,
            "component_count": self.component_count,
            "leaf_part_count": self.leaf_part_count,
            "unique_part_count": self.unique_part_count,
            "product_manifest_path": self.product_manifest_path,
            "product_status": self.product_status,
            "product_validation_status": self.product_validation_status,
            "product_validation_failures": list(self.product_validation_failures),
            "product_validation_checks": list(self.product_validation_checks),
            "execution_phase": self.execution_phase,
            "validation_short_circuited": self.validation_short_circuited,
        }


def bounded_validation_checks(
    validation_report: Mapping[str, object] | None,
) -> tuple[dict[str, object], ...]:
    """Project verified validation evidence into a bounded Agent-facing result."""

    if not isinstance(validation_report, Mapping):
        return ()
    raw_checks = validation_report.get("checks")
    if not isinstance(raw_checks, list):
        return ()
    ranked_checks = sorted(
        enumerate(raw_checks),
        key=lambda item: (
            not (isinstance(item[1], Mapping) and item[1].get("status") == "failed"),
            item[0],
        ),
    )
    checks: list[dict[str, object]] = []
    for _, raw_check in ranked_checks[:_VALIDATION_CHECK_LIMIT]:
        if not isinstance(raw_check, Mapping):
            continue
        check_id = raw_check.get("check_id")
        status = raw_check.get("status")
        if not isinstance(check_id, str) or status not in {
            "passed",
            "failed",
            "not_applicable",
        }:
            continue
        check: dict[str, object] = {"check_id": check_id, "status": status}
        message = raw_check.get("message")
        if isinstance(message, str):
            check["message"] = bounded_validation_text(message)
        evidence = raw_check.get("evidence")
        if isinstance(evidence, Mapping):
            budget = [_VALIDATION_NODE_LIMIT]
            bounded_evidence, truncated = _bounded_validation_value(
                evidence, key="evidence", depth=0, budget=budget
            )
            if isinstance(bounded_evidence, dict):
                check["evidence"] = bounded_evidence
            if truncated or raw_check.get("evidence_truncated") is True:
                check["evidence_truncated"] = True
        checks.append(check)
    return tuple(checks)


def _bounded_validation_value(
    value: object,
    *,
    key: str,
    depth: int,
    budget: list[int],
) -> tuple[object | None, bool]:
    if budget[0] <= 0 or depth > _VALIDATION_DEPTH_LIMIT:
        return None, True
    budget[0] -= 1
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        bounded = bounded_validation_text(value)
        return bounded, bounded != value
    if isinstance(value, Mapping):
        bounded_mapping: dict[str, object] = {}
        truncated = len(value) > _VALIDATION_MAPPING_LIMIT
        for raw_key, child in list(value.items())[:_VALIDATION_MAPPING_LIMIT]:
            if not isinstance(raw_key, str):
                truncated = True
                continue
            bounded_child, child_truncated = _bounded_validation_value(
                child, key=raw_key, depth=depth + 1, budget=budget
            )
            if budget[0] <= 0 and bounded_child is None:
                truncated = True
                break
            bounded_mapping[raw_key] = bounded_child
            truncated = truncated or child_truncated
        return bounded_mapping, truncated
    if isinstance(value, (list, tuple)):
        limit = _VALIDATION_SEQUENCE_LIMITS.get(key, _VALIDATION_SEQUENCE_LIMIT)
        bounded_items: list[object] = []
        truncated = len(value) > limit
        for child in value[:limit]:
            bounded_child, child_truncated = _bounded_validation_value(
                child, key=key, depth=depth + 1, budget=budget
            )
            if budget[0] <= 0 and bounded_child is None:
                truncated = True
                break
            bounded_items.append(bounded_child)
            truncated = truncated or child_truncated
        return bounded_items, truncated
    return bounded_validation_text(str(value)), True


def bounded_validation_text(value: str) -> str:
    if len(value) <= _VALIDATION_TEXT_LIMIT:
        return value
    return value[: _VALIDATION_TEXT_LIMIT - 3] + "..."


_bounded_validation_checks = bounded_validation_checks
_bounded_validation_text = bounded_validation_text


__all__ = ["ExecutionResult", "bounded_validation_checks", "bounded_validation_text"]
