"""Durable Project diagnostics normalization and accumulation."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Mapping


DIAGNOSTICS_NAME = "diagnostics.json"


class DiagnosticsStore:
    """Access one Project's diagnostics while the caller owns synchronization."""

    def read(self, project_dir: Path) -> dict[str, Any] | None:
        path = project_dir / DIAGNOSTICS_NAME
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Project diagnostics must be an object")
        return value

    def write(self, project_dir: Path, diagnostics: Mapping[str, Any]) -> None:
        _write_json(project_dir / DIAGNOSTICS_NAME, diagnostics)

    def merge(
        self,
        previous: Mapping[str, Any] | None,
        current: Mapping[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        token_usage = sum_token_usage(
            previous.get("token_usage") if previous is not None else None,
            current.get("token_usage"),
        )
        if token_usage is not None:
            merged["token_usage"] = token_usage
        return merged


def sum_token_usage(previous: Any, current: Any) -> dict[str, int] | None:
    previous_usage = normalized_token_usage(previous)
    current_usage = normalized_token_usage(current)
    if previous_usage is None:
        return current_usage
    if current_usage is None:
        return previous_usage
    return token_usage_from_counts(
        input_tokens=(previous_usage["input_tokens"] + current_usage["input_tokens"]),
        cached_input_tokens=(
            previous_usage["cached_input_tokens"]
            + current_usage["cached_input_tokens"]
        ),
        output_tokens=(previous_usage["output_tokens"] + current_usage["output_tokens"]),
    )


def normalized_token_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = _non_negative_int(value.get("input_tokens"))
    output_tokens = _non_negative_int(value.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    cached_input_tokens = min(
        _non_negative_int(value.get("cached_input_tokens")) or 0,
        input_tokens,
    )
    return token_usage_from_counts(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
    )


def token_usage_from_counts(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> dict[str, int]:
    return {
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": input_tokens - cached_input_tokens,
        "output_tokens": output_tokens,
    }


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


__all__ = [
    "DIAGNOSTICS_NAME",
    "DiagnosticsStore",
    "normalized_token_usage",
    "sum_token_usage",
    "token_usage_from_counts",
]
