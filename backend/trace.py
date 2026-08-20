"""Read-only, redacted access to Project conversation traces."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_logging import CONVERSATION_LOG_NAME
from .cad_executor import redact_credentials


SUMMARY_LIMIT = 240
_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|access_?token|refresh_?token|auth(?:orization)?|"
    r"password|passwd|secret|credential)(?:$|_)",
    re.IGNORECASE,
)
_ERROR_TYPES = frozenset(
    {"model_error", "provider_retry", "tool_error", "parse_error"}
)


class TraceError(ValueError):
    """Raised when a trace request points outside its Project or log."""


@dataclass(frozen=True)
class TraceBatch:
    """A batch of summaries and the next complete-line byte offset."""

    events: tuple[dict[str, Any], ...]
    next_offset: int
    reset: bool
    has_incomplete_tail: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": list(self.events),
            "next_offset": self.next_offset,
            "reset": self.reset,
            "has_incomplete_tail": self.has_incomplete_tail,
        }


def trace_path(project_dir: Path) -> Path:
    """Return the trace file only when it is a regular Project-owned file."""

    root = project_dir.resolve()
    path = root / CONVERSATION_LOG_NAME
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise TraceError("Conversation trace is not available")
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise TraceError("Conversation trace is outside the Project") from exc
    return path


def trace_stats(project_dir: Path) -> dict[str, Any]:
    """Return inexpensive catalog metadata without exposing trace contents."""

    try:
        path = trace_path(project_dir)
    except TraceError:
        return {
            "trace_available": False,
            "event_count": 0,
            "trace_bytes": 0,
        }

    event_count = 0
    with path.open("rb") as stream:
        for raw_line in stream:
            if raw_line.endswith(b"\n") and raw_line.strip():
                event_count += 1
    return {
        "trace_available": True,
        "event_count": event_count,
        "trace_bytes": path.stat().st_size,
    }


def read_trace(
    project_dir: Path,
    *,
    offset: int = 0,
    query: str = "",
) -> TraceBatch:
    """Read complete records after a byte offset and return redacted summaries."""

    if offset < 0:
        raise TraceError("Trace offset must be non-negative")
    path = trace_path(project_dir)
    size = path.stat().st_size
    reset = offset > size
    if reset:
        offset = 0

    normalized_query = query.casefold().strip()
    events: list[dict[str, Any]] = []
    next_offset = offset
    has_incomplete_tail = False
    with path.open("rb") as stream:
        stream.seek(offset)
        while True:
            cursor = stream.tell()
            raw_line = stream.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                has_incomplete_tail = True
                break
            next_offset = stream.tell()
            if not raw_line.strip():
                continue
            record = _decode_record(raw_line, cursor)
            searchable = json.dumps(record, ensure_ascii=False, sort_keys=True)
            if normalized_query and normalized_query not in searchable.casefold():
                continue
            events.append(_event_summary(record, cursor, len(raw_line)))

    return TraceBatch(
        events=tuple(events),
        next_offset=next_offset,
        reset=reset,
        has_incomplete_tail=has_incomplete_tail,
    )


def read_trace_event(project_dir: Path, cursor: int) -> dict[str, Any]:
    """Read one complete trace record at its opaque byte cursor."""

    if cursor < 0:
        raise TraceError("Trace cursor must be non-negative")
    path = trace_path(project_dir)
    size = path.stat().st_size
    if cursor >= size:
        raise TraceError("Trace event does not exist")
    with path.open("rb") as stream:
        stream.seek(cursor)
        raw_line = stream.readline()
    if not raw_line.endswith(b"\n"):
        raise TraceError("Trace event is not complete")
    record = _decode_record(raw_line, cursor)
    return {
        "cursor": cursor,
        "event": record,
        "raw": json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
    }


def iter_redacted_trace(project_dir: Path) -> Iterator[bytes]:
    """Return an iterator over a valid, recursively redacted JSONL trace."""

    path = trace_path(project_dir)

    def generate() -> Iterator[bytes]:
        with path.open("rb") as stream:
            while True:
                cursor = stream.tell()
                raw_line = stream.readline()
                if not raw_line:
                    return
                if not raw_line.endswith(b"\n"):
                    return
                if not raw_line.strip():
                    continue
                record = _decode_record(raw_line, cursor)
                yield (
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")

    return generate()


def _decode_record(raw_line: bytes, cursor: int) -> dict[str, Any]:
    text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "type": "parse_error",
            "sequence": None,
            "error": f"Invalid JSON at byte {cursor}: {exc.msg}",
            "raw_line": redact_credentials(text),
        }
    if not isinstance(value, Mapping):
        return {
            "type": "parse_error",
            "sequence": None,
            "error": f"Expected a JSON object at byte {cursor}",
            "raw_line": redact_credentials(text),
        }
    return _redact_value(value)


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and (
        key.casefold() == "token" or _SENSITIVE_KEY.search(key)
    ):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _event_summary(
    record: Mapping[str, Any], cursor: int, byte_size: int
) -> dict[str, Any]:
    event_type = str(record.get("type") or "unknown")
    payload = _payload(record)
    tool_name = _optional_text(payload.get("tool_name"))
    role = _event_role(record)
    title = _event_title(event_type, tool_name, role, record)
    summary = _summary_text(event_type, record)
    sequence = record.get("sequence")
    return {
        "cursor": cursor,
        "sequence": (
            sequence
            if isinstance(sequence, int) and not isinstance(sequence, bool)
            else None
        ),
        "timestamp": _optional_text(record.get("timestamp")),
        "turn_id": _optional_text(record.get("turn_id")),
        "type": event_type,
        "role": role,
        "title": title,
        "summary": summary,
        "tool_name": tool_name,
        "call_id": _optional_text(payload.get("call_id")),
        "is_error": event_type in _ERROR_TYPES
        or str(payload.get("status", "")).lower() in {"failed", "cancelled"},
        "byte_size": byte_size,
    }


def _event_role(record: Mapping[str, Any]) -> str | None:
    payload = _payload(record)
    if record.get("type") == "user_message":
        return "user"
    if record.get("type") == "assistant_message":
        return "assistant"
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    first = messages[0]
    if isinstance(first, Mapping):
        return _optional_text(first.get("role"))
    return None


def _event_title(
    event_type: str,
    tool_name: str | None,
    role: str | None,
    record: Mapping[str, Any],
) -> str:
    payload = _payload(record)
    if event_type == "turn_started":
        return "Turn started"
    if event_type in {"turn_succeeded", "turn_failed"}:
        status = _optional_text(payload.get("status")) or "finished"
        return f"Turn {status}"
    if event_type == "user_message":
        return "User"
    if event_type == "assistant_message":
        return "Assistant"
    if event_type in {"tool_call", "tool_result", "tool_error", "backend_tool"}:
        return tool_name or event_type.replace("_", " ").title()
    if event_type in {"model_request", "model_response"}:
        return (role or event_type.removeprefix("model_")).title()
    return event_type.replace("_", " ").title()


def _summary_text(event_type: str, record: Mapping[str, Any]) -> str:
    payload = _payload(record)
    if event_type == "turn_started":
        model = payload.get("model")
        if isinstance(model, Mapping):
            return _truncate(
                " / ".join(
                    str(value)
                    for value in (
                        payload.get("harness"),
                        model.get("provider"),
                        model.get("model_id"),
                    )
                    if value
                )
            )
    if event_type in {"turn_succeeded", "turn_failed"}:
        return _truncate(
            _optional_text(payload.get("failure_reason"))
            or _optional_text(payload.get("status"))
            or "Turn finished"
        )
    if event_type in {"tool_call", "backend_tool"}:
        value = payload.get("arguments", payload.get("target", ""))
        return _truncate(_compact_value(value))
    if event_type == "tool_result":
        return _truncate(_compact_value(payload.get("result", "")))
    if event_type in _ERROR_TYPES:
        error = payload.get("error", record.get("raw_line", ""))
        return _truncate(_compact_value(error))

    if event_type in {"user_message", "assistant_message", "context_summary"}:
        return _truncate(_compact_value(payload.get("content", "")))

    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, Mapping):
            content = _compact_value(last.get("content", ""))
            if content:
                return _truncate(content)
            tool_calls = last.get("tool_calls")
            if isinstance(tool_calls, list):
                names = [
                    str(item.get("name"))
                    for item in tool_calls
                    if isinstance(item, Mapping) and item.get("name")
                ]
                if names:
                    return _truncate("Calls " + ", ".join(names))
    return _truncate(_compact_value(payload or record))


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, Mapping) else record


def _compact_value(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if value in (None, {}, []):
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _truncate(value: str) -> str:
    if len(value) <= SUMMARY_LIMIT:
        return value
    return value[: SUMMARY_LIMIT - 1].rstrip() + "..."


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "TraceBatch",
    "TraceError",
    "iter_redacted_trace",
    "read_trace",
    "read_trace_event",
    "trace_stats",
]
