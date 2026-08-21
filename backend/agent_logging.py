"""Durable multi-turn conversation tracing for one CAD Project."""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from .cad_executor import redact_credentials

CONVERSATION_LOG_NAME = "conversation.jsonl"
LEGACY_AGENT_RUN_LOG_NAME = "agent-run.jsonl"
DEFAULT_MAX_CONTEXT_CHARS = 200_000
DEFAULT_RECENT_CONTEXT_TURNS = 6
LARGE_TOOL_RESULT_CHARS = 64_000
_CONVERSATION_LOCKS_GUARD = threading.Lock()
_CONVERSATION_LOCKS: dict[Path, threading.RLock] = {}
_INPUT_TOKEN_ALIASES = ("input_tokens", "prompt_tokens")
_OUTPUT_TOKEN_ALIASES = ("output_tokens", "completion_tokens")
_CACHED_TOKEN_ALIASES = (
    "cache_read",
    "cached_tokens",
    "priority_cache_read",
    "flex_cache_read",
)


class ConversationLogError(ValueError):
    """Raised when a persisted conversation cannot be read safely."""


class ConversationLog:
    """Append a complete, replayable multi-turn event stream for one Project."""

    def __init__(
        self,
        project_dir: str | Path,
        *,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        request_id: str | None = None,
        user_message: str | None = None,
        retry_of: str | None = None,
        harness: str | None = None,
        implementation_version: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.path = self.project_dir / CONVERSATION_LOG_NAME
        self.conversation_id = conversation_id or self.project_dir.name
        self.turn_id = turn_id
        self._lock = _conversation_lock(self.path)
        self._records: list[dict[str, Any]] = []
        self._tool_names: dict[str, str] = {}
        self._sequence = 0
        self._disabled = False
        self._input_tokens = 0
        self._cached_input_tokens = 0
        self._output_tokens = 0
        self._has_token_usage = False
        with self._lock:
            self._load_existing_records()
            self._remove_legacy_log()
            if turn_id is not None and not self._turn_exists(turn_id):
                self._append(
                    "turn_started",
                    request_id=request_id,
                    retry_of=retry_of,
                    harness=harness,
                    implementation_version=implementation_version,
                    model={
                        "provider": provider,
                        "model_id": model_id,
                        "base_url": base_url,
                        "reasoning_effort": reasoning_effort,
                    },
                )
                if user_message is not None:
                    self._append("user_message", content=user_message)

    def _load_existing_records(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            self.path.touch()
            return
        try:
            text = self.path.read_text(encoding="utf-8")
            lines = text.splitlines()
            records: list[dict[str, Any]] = []
            truncated_tail = False
            nonempty = [line for line in lines if line.strip()]
            for index, line in enumerate(nonempty):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    if index == len(nonempty) - 1 and not text.endswith(("\n", "\r")):
                        truncated_tail = True
                        break
                    del exc
                    continue
                if not isinstance(record, dict):
                    raise ConversationLogError(
                        "conversation log events must be objects"
                    )
                records.append(record)
            self._records = records
            if records:
                self._sequence = max(
                    int(record.get("sequence", index))
                    for index, record in enumerate(records, start=1)
                )
            if truncated_tail:
                self._rewrite_locked()
        except (OSError, UnicodeDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, ConversationLogError):
                raise
            raise ConversationLogError("conversation log could not be read") from exc

    def _remove_legacy_log(self) -> None:
        legacy_path = self.project_dir / LEGACY_AGENT_RUN_LOG_NAME
        if not legacy_path.is_file() or legacy_path.is_symlink():
            return
        try:
            legacy_path.unlink()
        except OSError:
            return

    def _turn_exists(self, turn_id: str) -> bool:
        return any(record.get("turn_id") == turn_id for record in self._records)

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
        reasoning_effort: str | None = None,
    ) -> None:
        """Fill the current turn's provider metadata once settings are available."""

        with self._lock:
            started = next(
                (
                    record
                    for record in reversed(self._records)
                    if record.get("turn_id") == self.turn_id
                    and record.get("type") == "turn_started"
                ),
                None,
            )
            if started is None:
                return
            payload = started.setdefault("payload", {})
            payload["model"] = _safe_value(
                {
                    "provider": provider,
                    "model_id": model_id,
                    "base_url": base_url,
                    "reasoning_effort": reasoning_effort,
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
        assistant_message: str | None = None,
        artifact_version: int | None = None,
    ) -> None:
        if assistant_message:
            self._append("assistant_message", content=assistant_message)
        if artifact_version is not None:
            self._append("artifact_committed", version=artifact_version)
        self._append(
            "turn_succeeded" if status == "succeeded" else "turn_failed",
            status=status,
            failure_reason=failure_reason,
        )

    def turns(self) -> list[dict[str, Any]]:
        """Build the user-facing turn projection from the canonical event stream."""

        with self._lock:
            turns: dict[str, dict[str, Any]] = {}
            order: list[str] = []
            for record in self._records:
                turn_id = record.get("turn_id")
                if not isinstance(turn_id, str) or not turn_id:
                    continue
                payload = record.get("payload")
                if not isinstance(payload, Mapping):
                    payload = {}
                turn = turns.get(turn_id)
                if turn is None:
                    turn = {
                        "turn_id": turn_id,
                        "sequence": len(order) + 1,
                        "request_id": None,
                        "retry_of": None,
                        "user_message": "",
                        "assistant_message": "",
                        "status": "running",
                        "created_at": record.get("timestamp"),
                        "completed_at": None,
                        "artifact_version": None,
                        "error": None,
                    }
                    turns[turn_id] = turn
                    order.append(turn_id)
                event_type = record.get("type")
                if event_type == "turn_started":
                    turn["request_id"] = payload.get("request_id")
                    turn["retry_of"] = payload.get("retry_of")
                    turn["created_at"] = record.get("timestamp")
                elif event_type == "user_message":
                    turn["user_message"] = _content_text(payload.get("content"))
                elif event_type == "assistant_message":
                    turn["assistant_message"] = _content_text(payload.get("content"))
                elif event_type == "artifact_committed":
                    version = payload.get("version")
                    if isinstance(version, int) and not isinstance(version, bool):
                        turn["artifact_version"] = version
                elif event_type in {"turn_succeeded", "turn_failed"}:
                    turn["status"] = str(payload.get("status") or "failed")
                    turn["completed_at"] = record.get("timestamp")
                    reason = payload.get("failure_reason")
                    turn["error"] = reason if isinstance(reason, str) else None
            return [turns[turn_id] for turn_id in order]

    def turn(self, turn_id: str) -> dict[str, Any] | None:
        return next((turn for turn in self.turns() if turn["turn_id"] == turn_id), None)

    def turn_for_request(self, request_id: str) -> dict[str, Any] | None:
        return next(
            (turn for turn in self.turns() if turn.get("request_id") == request_id),
            None,
        )

    def latest_model_response_text(self) -> str | None:
        with self._lock:
            for record in reversed(self._records):
                if (
                    record.get("turn_id") != self.turn_id
                    or record.get("type") != "model_response"
                ):
                    continue
                payload = record.get("payload")
                messages = payload.get("messages") if isinstance(payload, Mapping) else None
                if not isinstance(messages, list):
                    continue
                for message in reversed(messages):
                    if not isinstance(message, Mapping):
                        continue
                    content = _content_text(message.get("content"))
                    if message.get("role") == "assistant" and content.strip():
                        return content.strip()
        return None

    def context_messages(
        self,
        *,
        exclude_turn_id: str | None = None,
        max_chars: int | None = None,
        recent_turns: int = DEFAULT_RECENT_CONTEXT_TURNS,
    ) -> list[dict[str, str]]:
        """Return semantic history without replaying raw tool payloads."""

        limit = max_chars or _context_char_limit()
        turns = [
            turn
            for turn in self.turns()
            if turn["turn_id"] != exclude_turn_id and turn["status"] != "running"
        ]
        messages = _turn_messages(turns)
        if _messages_chars(messages) <= limit:
            return messages
        recent = turns[-recent_turns:] if recent_turns > 0 else []
        older = turns[:-recent_turns] if recent_turns > 0 else turns
        summary = _turn_summary(older)
        if self.turn_id is not None:
            self._append("context_summary", content=summary, summarized_turns=len(older))
        bounded = [{"role": "system", "content": summary}, *_turn_messages(recent)]
        while len(bounded) > 1 and _messages_chars(bounded) > limit:
            bounded.pop(1)
        if _messages_chars(bounded) > limit:
            bounded[0]["content"] = bounded[0]["content"][: max(0, limit // 2)]
        return bounded

    def _append(self, event_type: str, **fields: Any) -> None:
        with self._lock:
            self._sequence += 1
            payload = {
                key: _safe_value(value)
                for key, value in fields.items()
                if value is not None
            }
            payload = self._externalize_large_tool_payload(event_type, payload)
            record = {
                "conversation_id": self.conversation_id,
                "turn_id": self.turn_id,
                "sequence": self._sequence,
                "timestamp": _timestamp(),
                "type": event_type,
                "payload": payload,
            }
            self._records.append(record)
            self._write_line_locked(record)

    def _externalize_large_tool_payload(
        self, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if event_type not in {"tool_call", "tool_result"}:
            return payload
        key = "arguments" if event_type == "tool_call" else "result"
        value = payload.get(key)
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError, RecursionError):
            return payload
        if len(serialized) <= LARGE_TOOL_RESULT_CHARS:
            return payload
        turn_name = self.turn_id or "unassigned"
        relative = Path("large_tool_results") / turn_name / f"{self._sequence:06d}-{key}.json"
        target = self.project_dir / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(serialized + "\n", encoding="utf-8")
        except OSError:
            return payload
        payload[key] = {
            "external_path": relative.as_posix(),
            "characters": len(serialized),
            "summary": redact_credentials(serialized[:1000]),
        }
        return payload

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

    def __init__(self, trace: ConversationLog) -> None:
        self.trace = trace
        self._model_roles: dict[str, str] = {}

    def on_chat_model_start(
        self,
        _serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        run_key = str(run_id)
        metadata = kwargs.get("metadata")
        role = metadata.get("agent_role") if isinstance(metadata, Mapping) else None
        if not isinstance(role, str):
            tags = kwargs.get("tags")
            role = "reviewer" if isinstance(tags, list) and "cad-reviewer" in tags else None
        if role is not None:
            self._model_roles[run_key] = role
        for conversation in messages:
            self.trace.record_event(
                "model_request",
                messages=[_message_payload(message) for message in conversation],
                agent_role=role,
            )
        del parent_run_id

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        role = self._model_roles.pop(str(run_id), None)
        self.trace.record_model_usage(response)
        self.trace.record_event(
            "model_response",
            messages=_response_messages(response),
            agent_role=role,
        )
        del parent_run_id

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        role = self._model_roles.pop(str(run_id), None)
        self.trace.record_event(
            "model_error",
            error=str(error),
            agent_role=role,
        )
        del parent_run_id

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


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return "" if value is None else str(value)


def _turn_messages(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in turns:
        user = str(turn.get("user_message") or "").strip()
        assistant = str(turn.get("assistant_message") or "").strip()
        if user:
            messages.append({"role": "user", "content": user})
        if assistant:
            messages.append({"role": "assistant", "content": assistant})
        elif turn.get("error"):
            messages.append(
                {
                    "role": "assistant",
                    "content": f"The CAD turn failed: {turn['error']}",
                }
            )
    return messages


def _messages_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(message["content"]) for message in messages)


def _turn_summary(turns: list[dict[str, Any]]) -> str:
    lines = ["Earlier CAD conversation summary:"]
    for turn in turns:
        request = str(turn.get("user_message") or "").strip().replace("\n", " ")
        response = str(turn.get("assistant_message") or "").strip().replace("\n", " ")
        status = str(turn.get("status") or "unknown")
        version = turn.get("artifact_version")
        line = f"- Turn {turn.get('sequence')}: {status}; request={request[:500]}"
        if response:
            line += f"; response={response[:500]}"
        if version is not None:
            line += f"; artifact=v{int(version):04d}"
        if turn.get("error"):
            line += f"; error={str(turn['error'])[:500]}"
        lines.append(line)
    return "\n".join(lines)[:50_000]


def _context_char_limit() -> int:
    raw = os.environ.get(
        "CADFLOW_CONVERSATION_MAX_CONTEXT_CHARS",
        str(DEFAULT_MAX_CONTEXT_CHARS),
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CONTEXT_CHARS
    return value if value > 0 else DEFAULT_MAX_CONTEXT_CHARS


def _conversation_lock(path: Path) -> threading.RLock:
    with _CONVERSATION_LOCKS_GUARD:
        lock = _CONVERSATION_LOCKS.get(path)
        if lock is None:
            lock = threading.RLock()
            _CONVERSATION_LOCKS[path] = lock
        return lock


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


__all__ = [
    "CONVERSATION_LOG_NAME",
    "DEFAULT_MAX_CONTEXT_CHARS",
    "LEGACY_AGENT_RUN_LOG_NAME",
    "ConversationLog",
    "ConversationLogError",
]
