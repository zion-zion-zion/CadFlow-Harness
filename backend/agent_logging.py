"""Readable JSONL tracing for one Text-to-CAD Agent Run."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from .cad_executor import redact_credentials

AGENT_RUN_LOG_NAME = "agent-run.jsonl"
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")
_TOKEN_ALIASES = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "total_tokens": ("total_tokens", "total"),
}


class AgentRunLog:
    """Append one complete JSON object per line, preserving the full trace."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.path = self.project_dir / AGENT_RUN_LOG_NAME
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        self._tool_names: dict[str, str] = {}
        self._sequence = 0
        self._disabled = False
        self._token_usage_call_count = 0
        self._token_usage_field_counts = dict.fromkeys(_TOKEN_FIELDS, 0)
        self._token_usage_totals = dict.fromkeys(_TOKEN_FIELDS, 0)
        if self._load_existing_records():
            return
        self._append(
            "run_started",
            model={
                "provider": provider,
                "model_id": model_id,
                "base_url": base_url,
            },
        )

    def _load_existing_records(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            records = [
                json.loads(line)
                for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if not records or not all(isinstance(record, dict) for record in records):
                raise ValueError("empty or invalid Agent Run JSONL")
            self._records = records
            self._sequence = max(
                int(record.get("sequence", index))
                for index, record in enumerate(records, start=1)
            )
            return True
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            try:
                self.path.write_text("", encoding="utf-8")
            except OSError:
                self._disabled = True
            return False

    @property
    def data(self) -> dict[str, Any]:
        """Return the in-memory trace for tests and local diagnostics."""

        with self._lock:
            return {"records": json.loads(json.dumps(self._records))}

    def configure(
        self,
        *,
        provider: str | None = None,
        model_id: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Fill the first record's model fields once settings are available."""

        with self._lock:
            if not self._records or self._records[0].get("type") != "run_started":
                return
            self._records[0]["model"] = _safe_value(
                {
                    "provider": provider,
                    "model_id": model_id,
                    "base_url": base_url,
                }
            )
            self._rewrite_locked()

    def record_event(self, event_type: str, **fields: Any) -> None:
        self._append(event_type, **fields)

    def record_internal_tool(self, tool_name: str, target: str | None = None) -> None:
        self._append(
            "backend_tool",
            tool_name=tool_name,
            target=target,
        )

    @property
    def token_usage(self) -> dict[str, int | None] | None:
        """Return complete provider-reported token totals for this run."""

        with self._lock:
            if self._token_usage_call_count == 0:
                return None
            usage = {
                field: (
                    self._token_usage_totals[field]
                    if self._token_usage_field_counts[field]
                    == self._token_usage_call_count
                    else None
                )
                for field in _TOKEN_FIELDS
            }
            if (
                usage["total_tokens"] is None
                and usage["input_tokens"] is not None
                and usage["output_tokens"] is not None
            ):
                usage["total_tokens"] = (
                    usage["input_tokens"] + usage["output_tokens"]
                )
            if all(value is None for value in usage.values()):
                return None
            return usage

    def record_model_usage(self, response: Any) -> None:
        """Accumulate only normalized numeric usage from one model response."""

        usage = _response_token_usage(response)
        with self._lock:
            self._token_usage_call_count += 1
            if usage is None:
                return
            for field, value in usage.items():
                if value is not None:
                    self._token_usage_field_counts[field] += 1
                    self._token_usage_totals[field] += value

    def callback_handler(self) -> Any:
        return _AgentRunCallbackHandler(self)

    def finish(
        self,
        *,
        status: str,
        failure_reason: str | None = None,
    ) -> None:
        self._append(
            "run_finished",
            status=status,
            failure_reason=failure_reason,
        )

    def _append(self, event_type: str, **fields: Any) -> None:
        with self._lock:
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "timestamp": _timestamp(),
                "type": event_type,
                **{
                    key: _safe_value(value)
                    for key, value in fields.items()
                    if value is not None
                },
            }
            self._records.append(record)
            self._write_line_locked(record)

    def _write_line_locked(self, record: Mapping[str, Any]) -> None:
        if self._disabled:
            return
        try:
            self.project_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                stream.flush()
        except (OSError, TypeError, ValueError, OverflowError, RecursionError):
            # Logging must not turn a valid CAD result into a failed run.
            self._disabled = True

    def _rewrite_locked(self) -> None:
        if self._disabled:
            return
        try:
            self.project_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as stream:
                for record in self._records:
                    stream.write(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                stream.flush()
        except (OSError, TypeError, ValueError, OverflowError, RecursionError):
            self._disabled = True


class _AgentRunCallbackHandler(BaseCallbackHandler):
    """Capture exact model messages and tool arguments/results."""

    run_inline = True
    raise_error = False

    def __init__(self, trace: AgentRunLog) -> None:
        self.trace = trace

    def on_chat_model_start(
        self,
        _serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        del run_id, parent_run_id
        for conversation in messages:
            self.trace.record_event(
                "model_request",
                messages=[_message_payload(message) for message in conversation],
            )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        del run_id, parent_run_id
        self.trace.record_model_usage(response)
        self.trace.record_event(
            "model_response",
            messages=_response_messages(response),
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        del run_id, parent_run_id
        self.trace.record_event(
            "model_error",
            error=str(error),
        )

    def on_retry(self, retry_state: Any, *, run_id: Any, **_: Any) -> None:
        del run_id
        self.trace.record_event(
            "provider_retry",
            error=_retry_error(retry_state),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        tool_name = _tool_name(serialized, kwargs) or "unknown_tool"
        run_key = str(run_id)
        self.trace._tool_names[run_key] = tool_name
        arguments: Any = inputs
        if arguments is None:
            try:
                arguments = json.loads(input_str)
            except (TypeError, json.JSONDecodeError):
                arguments = input_str
        self.trace.record_event(
            "tool_call",
            tool_name=tool_name,
            arguments=arguments,
        )
        del parent_run_id

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        run_key = str(run_id)
        self.trace.record_event(
            "tool_result",
            tool_name=(
                _tool_name({}, kwargs)
                or self.trace._tool_names.pop(run_key, None)
                or "unknown_tool"
            ),
            result=_tool_result_payload(output),
        )
        del parent_run_id

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        run_key = str(run_id)
        self.trace.record_event(
            "tool_error",
            tool_name=(
                _tool_name({}, kwargs)
                or self.trace._tool_names.pop(run_key, None)
                or "unknown_tool"
            ),
            error=str(error),
        )
        del parent_run_id


def _tool_name(
    serialized: Mapping[str, Any], kwargs: Mapping[str, Any]
) -> str | None:
    for source in (serialized, kwargs):
        for key in ("name", "tool_name"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _message_payload(message: Any) -> dict[str, Any]:
    """Keep only message data that is actually part of the model conversation."""

    if isinstance(message, Mapping):
        role = message.get("role") or message.get("type") or "unknown"
        payload: dict[str, Any] = {
            "role": _message_role(str(role)),
            "content": message.get("content", ""),
        }
        for key in ("name", "tool_call_id", "tool_calls", "invalid_tool_calls"):
            if message.get(key) not in (None, [], {}):
                payload[key] = message[key]
        return _safe_value(payload)

    role = _message_role(str(getattr(message, "type", "unknown")))
    payload = {
        "role": role,
        "content": getattr(message, "content", ""),
    }
    for key in ("name", "tool_call_id", "tool_calls", "invalid_tool_calls"):
        value = getattr(message, key, None)
        if value not in (None, [], {}):
            payload[key] = value
    return _safe_value(payload)


def _message_role(message_type: str) -> str:
    return {
        "human": "user",
        "ai": "assistant",
    }.get(message_type, message_type)


def _response_messages(response: Any) -> list[dict[str, Any]]:
    """Extract assistant output without provider metadata or token statistics."""

    generations = getattr(response, "generations", None)
    if generations is None and isinstance(response, Mapping):
        generations = response.get("generations")
    if not isinstance(generations, (list, tuple)):
        return [_message_payload(response)]

    messages: list[dict[str, Any]] = []
    for batch in generations:
        items = batch if isinstance(batch, (list, tuple)) else (batch,)
        for generation in items:
            message = getattr(generation, "message", None)
            if message is None and isinstance(generation, Mapping):
                message = generation.get("message")
            if message is not None:
                messages.append(_message_payload(message))
                continue
            text = getattr(generation, "text", None)
            if text is None and isinstance(generation, Mapping):
                text = generation.get("text")
            messages.append(
                _safe_value({"role": "assistant", "content": text or ""})
            )
    return messages


def _response_token_usage(response: Any) -> dict[str, int | None] | None:
    """Extract provider usage without retaining the original metadata object."""

    generations = getattr(response, "generations", None)
    if generations is None and isinstance(response, Mapping):
        generations = response.get("generations")

    message_usages: list[dict[str, int | None]] = []
    if isinstance(generations, (list, tuple)):
        for batch in generations:
            items = batch if isinstance(batch, (list, tuple)) else (batch,)
            for generation in items:
                message = getattr(generation, "message", None)
                if message is None and isinstance(generation, Mapping):
                    message = generation.get("message")
                if message is not None:
                    usage = _message_token_usage(message)
                    if usage is not None:
                        message_usages.append(usage)
    else:
        usage = _message_token_usage(response)
        if usage is not None:
            message_usages.append(usage)

    if message_usages:
        return _combine_usage(message_usages)

    llm_output = getattr(response, "llm_output", None)
    if llm_output is None and isinstance(response, Mapping):
        llm_output = response.get("llm_output")
    if isinstance(llm_output, Mapping):
        raw_usage = llm_output.get("token_usage") or llm_output.get("usage")
        return _normalize_usage(raw_usage)
    return None


def _message_token_usage(message: Any) -> dict[str, int | None] | None:
    metadata = getattr(message, "usage_metadata", None)
    if metadata is None and isinstance(message, Mapping):
        metadata = message.get("usage_metadata")
    usage = _normalize_usage(metadata)
    if usage is not None:
        return usage

    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata is None and isinstance(message, Mapping):
        response_metadata = message.get("response_metadata")
    if isinstance(response_metadata, Mapping):
        return _normalize_usage(
            response_metadata.get("token_usage") or response_metadata.get("usage")
        )
    return None


def _combine_usage(usages: list[dict[str, int | None]]) -> dict[str, int | None]:
    return {
        field: (
            sum(usage[field] for usage in usages if usage[field] is not None)
            if all(usage[field] is not None for usage in usages)
            else None
        )
        for field in _TOKEN_FIELDS
    }


def _normalize_usage(value: Any) -> dict[str, int | None] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, int | None] = {}
    for field, aliases in _TOKEN_ALIASES.items():
        item = next((value.get(alias) for alias in aliases if alias in value), None)
        if item is None:
            normalized[field] = None
        elif isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            normalized[field] = item
        else:
            normalized[field] = None
    if all(item is None for item in normalized.values()):
        return None
    if (
        normalized["total_tokens"] is None
        and normalized["input_tokens"] is not None
        and normalized["output_tokens"] is not None
    ):
        normalized["total_tokens"] = (
            normalized["input_tokens"] + normalized["output_tokens"]
        )
    return normalized


def _tool_result_payload(output: Any) -> Any:
    """Return the tool payload without LangChain's message wrapper metadata."""

    if getattr(output, "type", None) == "tool":
        return _safe_value(getattr(output, "content", ""))
    if isinstance(output, Mapping) and output.get("type") == "tool":
        return _safe_value(output.get("content", ""))
    return _safe_value(output)


def _retry_error(retry_state: Any) -> str | None:
    outcome = getattr(retry_state, "outcome", None)
    if outcome is not None:
        exception = getattr(outcome, "exception", None)
        if callable(exception):
            try:
                error = exception()
            except (RuntimeError, TypeError, ValueError):
                error = None
            if error is not None:
                return str(error)
    return None


def _safe_value(value: Any) -> Any:
    """Convert callback values to JSON without truncating their contents."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, bytes):
        return redact_credentials(value.decode("utf-8", errors="replace"))
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _safe_value(model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError, RecursionError):
            pass
    return redact_credentials(str(value))


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


__all__ = ["AGENT_RUN_LOG_NAME", "AgentRunLog"]
