"""Durable, curated Progress Events and their SSE representation."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cad_executor import redact_credentials


EVENTS_NAME = "events.jsonl"
MAX_EVENT_RESULT_CHARS = 180
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ProgressEventError(ValueError):
    """Raised when a Progress Event or Project ID is invalid."""


@dataclass(frozen=True)
class ProgressUpdate:
    """An in-process update before it is persisted for one Project."""

    stage: str
    tool: str | None = None
    attempt: int | None = None
    result: str | None = None
    preview_attempt: int | None = None
    preview_revision: int | None = None
    preview_operation: str | None = None


@dataclass(frozen=True)
class ProgressEvent:
    """A small operational update safe to send to the browser."""

    event_id: int
    created_at: str
    stage: str
    tool: str | None = None
    attempt: int | None = None
    result: str | None = None
    preview_attempt: int | None = None
    preview_revision: int | None = None
    preview_operation: str | None = None

    @property
    def id(self) -> int:
        """Expose the SSE identifier under the conventional short name."""

        return self.event_id

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.event_id,
            "created_at": self.created_at,
            "stage": self.stage,
            "tool": self.tool,
            "attempt": self.attempt,
            "result": self.result,
        }
        if self.preview_revision is not None:
            payload["preview"] = {
                "attempt": self.preview_attempt,
                "revision": self.preview_revision,
                "operation": self.preview_operation,
            }
        return payload

    def to_sse(self) -> str:
        data = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
        if self.tool == "preview":
            event_name = (
                "scene-preview" if self.preview_revision is not None else "preview-status"
            )
        else:
            event_name = "scene-preview" if self.preview_revision is not None else "progress"
        return f"id: {self.event_id}\nevent: {event_name}\ndata: {data}\n\n"


class ProgressEventStore:
    """Persist and replay one Project's curated event timeline.

    Event IDs are scoped to a Project. The file is append-only during a service
    process, and the next ID is reconstructed from disk after a restart.
    """

    def __init__(self, projects_root: str | Path) -> None:
        self.root = Path(projects_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._condition = threading.Condition(threading.RLock())

    def append(
        self,
        project_id: str,
        *,
        stage: str,
        tool: str | None = None,
        attempt: int | None = None,
        result: str | None = None,
        preview_attempt: int | None = None,
        preview_revision: int | None = None,
        preview_operation: str | None = None,
    ) -> ProgressEvent:
        """Append one whitelisted operational update and return its ID."""

        self._validate_project_id(project_id)
        if not isinstance(stage, str) or not stage.strip():
            raise ProgressEventError("Progress Event stage must not be empty")
        if tool is not None and (not isinstance(tool, str) or not tool.strip()):
            raise ProgressEventError("Progress Event tool must be text")
        if attempt is not None and (
            not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1
        ):
            raise ProgressEventError("Progress Event attempt must be positive")
        _validate_preview_fields(
            preview_attempt=preview_attempt,
            preview_revision=preview_revision,
            preview_operation=preview_operation,
        )
        event = None
        with self._condition:
            path = self._events_path(project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            previous = self._read_locked(path)
            next_id = previous[-1].event_id + 1 if previous else 1
            event = ProgressEvent(
                event_id=next_id,
                created_at=_timestamp(),
                stage=stage.strip()[:80],
                tool=tool.strip()[:80] if tool is not None else None,
                attempt=attempt,
                result=_short_result(result),
                preview_attempt=preview_attempt,
                preview_revision=preview_revision,
                preview_operation=preview_operation,
            )
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        event.to_dict(), ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                )
                stream.flush()
            self._condition.notify_all()
        return event

    def read_after(
        self, project_id: str, last_event_id: int = 0
    ) -> tuple[ProgressEvent, ...]:
        """Return all persisted events with an ID greater than ``last_event_id``."""

        self._validate_project_id(project_id)
        if (
            not isinstance(last_event_id, int)
            or isinstance(last_event_id, bool)
            or last_event_id < 0
        ):
            raise ProgressEventError("Last-Event-ID must be a non-negative integer")
        with self._condition:
            return tuple(
                event
                for event in self._read_locked(self._events_path(project_id))
                if event.event_id > last_event_id
            )

    def wait_for_events(
        self,
        project_id: str,
        last_event_id: int,
        timeout_seconds: float,
    ) -> tuple[ProgressEvent, ...]:
        """Wait for an event after ``last_event_id`` without blocking the event loop."""

        self._validate_project_id(project_id)
        if timeout_seconds <= 0:
            return self.read_after(project_id, last_event_id)
        with self._condition:
            events = self.read_after(project_id, last_event_id)
            if events:
                return events
            self._condition.wait(timeout_seconds)
            return self.read_after(project_id, last_event_id)

    def project_events_path(self, project_id: str) -> Path:
        """Return the durable event path after validating the opaque ID."""

        self._validate_project_id(project_id)
        return self._events_path(project_id)

    @staticmethod
    def keepalive() -> str:
        return ": keepalive\n\n"

    def _events_path(self, project_id: str) -> Path:
        project_dir = (self.root / project_id).resolve()
        try:
            project_dir.relative_to(self.root)
        except ValueError as exc:
            raise ProgressEventError("Project is outside the catalog") from exc
        return project_dir / EVENTS_NAME

    @staticmethod
    def _validate_project_id(project_id: str) -> None:
        if (
            not isinstance(project_id, str)
            or _PROJECT_ID_PATTERN.fullmatch(project_id) is None
        ):
            raise ProgressEventError("invalid Project ID")

    @staticmethod
    def _read_locked(path: Path) -> tuple[ProgressEvent, ...]:
        if not path.is_file():
            return ()
        events: list[ProgressEvent] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                events.append(_event_from_dict(data))
            except (json.JSONDecodeError, ProgressEventError) as exc:
                raise ProgressEventError(
                    f"invalid Progress Event at line {line_number}"
                ) from exc
        _ensure_monotonic(events)
        return tuple(events)


def _event_from_dict(data: Any) -> ProgressEvent:
    if not isinstance(data, dict):
        raise ProgressEventError("Progress Event must be an object")
    event_id = data.get("id")
    created_at = data.get("created_at")
    stage = data.get("stage")
    tool = data.get("tool")
    attempt = data.get("attempt")
    result = data.get("result")
    preview = data.get("preview")
    preview_attempt = preview_revision = preview_operation = None
    if preview is not None:
        if not isinstance(preview, dict):
            raise ProgressEventError("preview event payload is invalid")
        preview_attempt = preview.get("attempt")
        preview_revision = preview.get("revision")
        preview_operation = preview.get("operation")
    if (
        not isinstance(event_id, int)
        or isinstance(event_id, bool)
        or event_id < 1
        or not isinstance(created_at, str)
        or not isinstance(stage, str)
        or (tool is not None and not isinstance(tool, str))
        or (
            attempt is not None
            and (not isinstance(attempt, int) or isinstance(attempt, bool))
        )
        or (result is not None and not isinstance(result, str))
    ):
        raise ProgressEventError("Progress Event fields are invalid")
    _validate_preview_fields(
        preview_attempt=preview_attempt,
        preview_revision=preview_revision,
        preview_operation=preview_operation,
    )
    return ProgressEvent(
        event_id=event_id,
        created_at=created_at,
        stage=stage,
        tool=tool,
        attempt=attempt,
        result=result,
        preview_attempt=preview_attempt,
        preview_revision=preview_revision,
        preview_operation=preview_operation,
    )


def _ensure_monotonic(events: list[ProgressEvent]) -> None:
    expected = 1
    for event in events:
        if event.event_id != expected:
            raise ProgressEventError("Progress Event IDs must be monotonic")
        expected += 1


def _short_result(result: str | None) -> str | None:
    if result is None:
        return None
    if not isinstance(result, str):
        raise ProgressEventError("Progress Event result must be text")
    first_line = result.strip().splitlines()[0] if result.strip() else ""
    return redact_credentials(first_line)[:MAX_EVENT_RESULT_CHARS] or None


def _validate_preview_fields(
    *,
    preview_attempt: object,
    preview_revision: object,
    preview_operation: object,
) -> None:
    values = (preview_attempt, preview_revision, preview_operation)
    if all(value is None for value in values):
        return
    if (
        not isinstance(preview_attempt, int)
        or isinstance(preview_attempt, bool)
        or preview_attempt < 1
        or not isinstance(preview_revision, int)
        or isinstance(preview_revision, bool)
        or preview_revision < 1
        or not isinstance(preview_operation, str)
        or not preview_operation.strip()
    ):
        raise ProgressEventError("preview event fields are invalid")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


__all__ = [
    "EVENTS_NAME",
    "MAX_EVENT_RESULT_CHARS",
    "ProgressEvent",
    "ProgressEventError",
    "ProgressEventStore",
    "ProgressUpdate",
]
