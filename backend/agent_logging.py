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
_INPUT_TOKEN_ALIASES = ("input_tokens", "prompt_tokens")
_OUTPUT_TOKEN_ALIASES = ("output_tokens", "completion_tokens")
_CACHED_TOKEN_ALIASES = (
    "cache_read",
    "cached_tokens",
    "priority_cache_read",
    "flex_cache_read",
)


class AgentRunLog:
    """Append one complete JSON object per line, preserving the full trace."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        harness: str | None = None,
        implementation_version: str | None = None,
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
        self._input_tokens = 0
        self._cached_input_tokens = 0
        self._output_tokens = 0
        self._has_token_usage = False
        if self._load_existing_records():
            return
        self._append(
            "run_started",
            harness=harness,
            implementation_version=implementation_version,
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
    def token_usage(self) -> dict[str, int] | None:
        """Return accumulated input, cache, output, and derived token totals."""

        with self._lock:
            if not self._has_token_usage:
                return None
            return {
                "total_tokens": self._input_tokens + self._output_tokens,
                "input_tokens": self._input_tokens,
                "cached_input_tokens": self._cached_input_tokens,
                "uncached_input_tokens": (
                    self._input_tokens - self._cached_input_tokens
                ),
                "output_tokens": self._output_tokens,
            }

    def record_model_usage(self, response: Any) -> None:
        """Accumulate only normalized numeric usage from one model response."""

        usage = _response_token_usage(response)
        with self._lock:
            if usage is None:
                return
            self._has_token_usage = True
            self._input_tokens += usage["input_tokens"]
            self._cached_input_tokens += usage["cached_input_tokens"]
            self._output_tokens += usage["output_tokens"]

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
            call_id=run_key,
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
            call_id=run_key,
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
            call_id=run_key,
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


def _response_token_usage(response: Any) -> dict[str, int] | None:
    """Extract provider usage without retaining the original metadata object."""

    llm_output = getattr(response, "llm_output", None)
    if llm_output is None and isinstance(response, Mapping):
        llm_output = response.get("llm_output")
    if isinstance(llm_output, Mapping):
        raw_usage = llm_output.get("token_usage") or llm_output.get("usage")
        usage = _normalize_usage(raw_usage)
        if usage is not None:
            return usage

    generations = getattr(response, "generations", None)
    if generations is None and isinstance(response, Mapping):
        generations = response.get("generations")

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
                        return usage
        return None
    return _message_token_usage(response)


def _message_token_usage(message: Any) -> dict[str, int] | None:
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


def _normalize_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None

    input_tokens = _token_count(value, _INPUT_TOKEN_ALIASES)
    output_tokens = _token_count(value, _OUTPUT_TOKEN_ALIASES)
    if input_tokens is None or output_tokens is None:
        return None

    cached_tokens = _cached_token_count(value)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": min(cached_tokens or 0, input_tokens),
        "output_tokens": output_tokens,
    }


def _cached_token_count(value: Mapping[str, Any]) -> int | None:
    direct = _token_count(value, _CACHED_TOKEN_ALIASES)
    if direct is not None:
        return direct
    for details_key in ("input_token_details", "prompt_tokens_details"):
        details = value.get(details_key)
        if isinstance(details, Mapping):
            cached = _token_count(details, _CACHED_TOKEN_ALIASES)
            if cached is not None:
                return cached
    return None


def _token_count(value: Mapping[str, Any], aliases: tuple[str, ...]) -> int | None:
    item = next((value.get(alias) for alias in aliases if alias in value), None)
    if isinstance(item, int) and not isinstance(item, bool) and item >= 0:
        return item
    return None


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
