"""Independent, best-effort live previews for a Running Project."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .cad_executor import CancellationToken, build_cad_environment, redact_credentials
from .previews import PreviewError, validate_preview_glb
from .projects import ProjectState, ProjectStore


PREVIEW_DEBOUNCE_SECONDS = 0.5
PREVIEW_POLL_SECONDS = 0.25
PREVIEW_TIMEOUT_SECONDS = 15.0
PREVIEW_OUTPUT_BYTES = 64 * 1024
LIVE_PREVIEW_DIRECTORY = "previews/live"
LIVE_PREVIEW_STATUS_NAME = "status.json"
LIVE_PREVIEW_MODEL_NAME = "model.glb"

_EXCLUDED_ROOT_NAMES = frozenset(
    {
        ".cad-review",
        ".git",
        "__pycache__",
        "artifacts",
        "conversation_history",
        "large_tool_results",
        "previews",
    }
)
_EXCLUDED_FILES = frozenset(
    {
        "diagnostics.json",
        "events.jsonl",
        "project.json",
        "prompt.txt",
        "trace.jsonl",
    }
)


@dataclass(frozen=True)
class LivePreviewStatus:
    state: str = "waiting"
    revision: int = 0
    source_hash: str | None = None
    updated_at: str | None = None
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    artifact_available: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "revision": self.revision,
            "source_hash": self.source_hash,
            "updated_at": self.updated_at,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifact_available": self.artifact_available,
        }


@dataclass(frozen=True)
class LivePreviewResult:
    status: str
    payload: bytes | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""


class LivePreviewRunner(Protocol):
    def execute(
        self,
        project_dir: str | Path,
        *,
        timeout_seconds: float = PREVIEW_TIMEOUT_SECONDS,
        cancellation_token: CancellationToken | None = None,
    ) -> LivePreviewResult: ...


class LivePreviewStore:
    """Persist only the latest usable preview and its bounded UI status."""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.root = self.project_dir / LIVE_PREVIEW_DIRECTORY
        self.status_path = self.root / LIVE_PREVIEW_STATUS_NAME
        self.model_path = self.root / LIVE_PREVIEW_MODEL_NAME
        self._lock = threading.RLock()

    def read_status(self) -> LivePreviewStatus:
        with self._lock:
            if not self.status_path.is_file() or self.status_path.is_symlink():
                return LivePreviewStatus(artifact_available=self._artifact_available())
            try:
                value = json.loads(self.status_path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("status must be an object")
                return LivePreviewStatus(
                    state=str(value.get("state", "waiting")),
                    revision=max(0, int(value.get("revision", 0))),
                    source_hash=_optional_text(value.get("source_hash")),
                    updated_at=_optional_text(value.get("updated_at")),
                    error=_optional_text(value.get("error")),
                    stdout=_optional_text(value.get("stdout")),
                    stderr=_optional_text(value.get("stderr")),
                    artifact_available=self._artifact_available(),
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return LivePreviewStatus(
                    state="failed",
                    error="Live preview status could not be read",
                    artifact_available=self._artifact_available(),
                )

    def write_status(
        self,
        state: str,
        *,
        source_hash: str | None = None,
        error: str | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> LivePreviewStatus:
        with self._lock:
            previous = self.read_status()
            status = LivePreviewStatus(
                state=state,
                revision=previous.revision,
                source_hash=source_hash or previous.source_hash,
                updated_at=_timestamp(),
                error=_bounded(error),
                stdout=_bounded(stdout),
                stderr=_bounded(stderr),
                artifact_available=self._artifact_available(),
            )
            self._write_status(status)
            return status

    def publish(self, payload: bytes, source_hash: str) -> LivePreviewStatus:
        validate_preview_glb(payload)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            previous = self.read_status()
            temporary = self.root / f".{LIVE_PREVIEW_MODEL_NAME}.{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(payload)
            temporary.replace(self.model_path)
            status = LivePreviewStatus(
                state="current",
                revision=previous.revision + 1,
                source_hash=source_hash,
                updated_at=_timestamp(),
                artifact_available=True,
            )
            self._write_status(status)
            return status

    def artifact(self) -> Path:
        with self._lock:
            if not self._artifact_available():
                raise PreviewError("live preview is missing")
            validate_preview_glb(self.model_path.read_bytes())
            return self.model_path

    def clear(self) -> None:
        with self._lock:
            if self.root.is_symlink():
                self.root.unlink()
            elif self.root.is_dir():
                shutil.rmtree(self.root)

    def _artifact_available(self) -> bool:
        return self.model_path.is_file() and not self.model_path.is_symlink()

    def _write_status(self, status: LivePreviewStatus) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f".{LIVE_PREVIEW_STATUS_NAME}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(status.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.status_path)


class LivePreviewExecutor:
    """Run one source snapshot without producing validation artifacts."""

    def execute(
        self,
        project_dir: str | Path,
        *,
        timeout_seconds: float = PREVIEW_TIMEOUT_SECONDS,
        cancellation_token: CancellationToken | None = None,
    ) -> LivePreviewResult:
        root = Path(project_dir).expanduser().resolve()
        token = cancellation_token or CancellationToken()
        process: subprocess.Popen[bytes] | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="cadflow-live-preview-") as temporary:
                snapshot = Path(temporary) / "project"
                _copy_preview_inputs(root, snapshot)
                model_path = snapshot / "model.py"
                if not model_path.is_file():
                    return LivePreviewResult("failed", error="model.py is missing")
                output_path = Path(temporary) / LIVE_PREVIEW_MODEL_NAME
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-c",
                        _PREVIEW_RUNNER,
                        str(model_path),
                        str(output_path),
                    ],
                    cwd=snapshot,
                    env=build_cad_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    start_new_session=os.name == "posix",
                    preexec_fn=_lower_process_priority if os.name == "posix" else None,
                )
                token.register_process(process, _terminate_process)
                try:
                    stdout_raw, stderr_raw = process.communicate(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    token.cancel("timeout")
                    stdout_raw, stderr_raw = process.communicate()
                    return LivePreviewResult(
                        "timed_out",
                        error=f"Live preview timed out after {timeout_seconds:g} seconds",
                        stdout=_safe_output(stdout_raw),
                        stderr=_safe_output(stderr_raw),
                    )
                finally:
                    token.clear_process(process)
                stdout = _safe_output(stdout_raw)
                stderr = _safe_output(stderr_raw)
                if token.cancelled:
                    return LivePreviewResult("cancelled", stdout=stdout, stderr=stderr)
                if process.returncode != 0:
                    return LivePreviewResult(
                        "failed",
                        error=_first_error(stderr, stdout),
                        stdout=stdout,
                        stderr=stderr,
                    )
                payload = output_path.read_bytes()
                validate_preview_glb(payload)
                return LivePreviewResult(
                    "succeeded", payload=payload, stdout=stdout, stderr=stderr
                )
        except (OSError, PreviewError, ValueError) as exc:
            return LivePreviewResult("failed", error=redact_credentials(str(exc)))
        finally:
            if process is not None and process.poll() is None:
                _terminate_process(process)


class LivePreviewScheduler:
    """Watch one Running Project and publish latest-source-only previews."""

    def __init__(
        self,
        store: ProjectStore,
        *,
        executor: LivePreviewRunner | None = None,
        on_status: Callable[[str, LivePreviewStatus], None] | None = None,
        debounce_seconds: float = PREVIEW_DEBOUNCE_SECONDS,
        poll_seconds: float = PREVIEW_POLL_SECONDS,
    ) -> None:
        self.store = store
        self.executor = executor or LivePreviewExecutor()
        self.on_status = on_status
        self.debounce_seconds = debounce_seconds
        self.poll_seconds = poll_seconds
        self._condition = threading.Condition(threading.RLock())
        self._active_project_id: str | None = None
        self._observed_hash: str | None = None
        self._pending_hash: str | None = None
        self._due_at: float | None = None
        self._running_hash: str | None = None
        self._running_token: CancellationToken | None = None
        self._validating = False
        self._user_paused = False
        self._closed = False
        self._monitor: threading.Thread | None = None
        self._worker: threading.Thread | None = None

    def activate(self, project_id: str) -> None:
        self._ensure_started()
        root = self.store.project_directory(project_id)
        LivePreviewStore(root).clear()
        with self._condition:
            self._active_project_id = project_id
            self._observed_hash = None
            self._pending_hash = None
            self._due_at = None
            self._validating = False
            self._user_paused = False
            self._condition.notify_all()
        self._emit(project_id, LivePreviewStore(root).write_status("waiting"))

    def deactivate(self, project_id: str, *, validated: bool = False) -> None:
        token: CancellationToken | None = None
        with self._condition:
            if self._active_project_id != project_id:
                return
            token = self._running_token
            self._active_project_id = None
            self._pending_hash = None
            self._due_at = None
            self._condition.notify_all()
        if token is not None:
            token.cancel()
        if not validated:
            store = LivePreviewStore(self.store.project_directory(project_id))
            self._emit(project_id, store.write_status("stale"))

    def pause_for_validation(self, project_dir: str | Path) -> None:
        project_id = Path(project_dir).expanduser().resolve().name
        token: CancellationToken | None = None
        with self._condition:
            if self._active_project_id != project_id:
                return
            self._validating = True
            token = self._running_token
            if self._running_hash is not None:
                self._pending_hash = self._running_hash
            self._due_at = None
            self._condition.notify_all()
        if token is not None:
            threading.Thread(
                target=token.cancel,
                name="live-preview-validation-cancel",
                daemon=True,
            ).start()
        threading.Thread(
            target=self._publish_validation_status,
            args=(project_id,),
            name="live-preview-validation-status",
            daemon=True,
        ).start()

    def resume_after_validation(self, project_dir: str | Path) -> None:
        project_id = Path(project_dir).expanduser().resolve().name
        with self._condition:
            if self._active_project_id != project_id:
                return
            self._validating = False
            if self._pending_hash is not None:
                self._due_at = time.monotonic()
            self._condition.notify_all()

    def set_paused(self, project_id: str, paused: bool) -> LivePreviewStatus:
        token: CancellationToken | None = None
        with self._condition:
            if self._active_project_id != project_id:
                raise PreviewError("live preview is not active")
            self._user_paused = paused
            token = self._running_token if paused else None
            if not paused:
                self._pending_hash = self._observed_hash
                self._due_at = time.monotonic()
            self._condition.notify_all()
        if token is not None:
            token.cancel()
        return self._set_status(project_id, "paused" if paused else "waiting")

    def retry(self, project_id: str) -> None:
        with self._condition:
            if self._active_project_id != project_id:
                raise PreviewError("live preview is not active")
            self._pending_hash = self._observed_hash or _source_hash(
                self.store.project_directory(project_id)
            )
            self._due_at = time.monotonic()
            self._condition.notify_all()

    def close(self) -> None:
        token: CancellationToken | None = None
        with self._condition:
            self._closed = True
            token = self._running_token
            self._condition.notify_all()
        if token is not None:
            token.cancel()
        if self._monitor is not None:
            self._monitor.join(timeout=1.0)
        if self._worker is not None:
            self._worker.join(timeout=1.0)

    def _ensure_started(self) -> None:
        with self._condition:
            if self._closed:
                raise PreviewError("live preview scheduler is closed")
            if self._monitor is not None:
                return
            self._monitor = threading.Thread(
                target=self._monitor_loop,
                name="live-preview-monitor",
                daemon=True,
            )
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="live-preview-worker",
                daemon=True,
            )
            self._monitor.start()
            self._worker.start()

    def _monitor_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                project_id = self._active_project_id
            if project_id is not None:
                try:
                    project = self.store.get_project(project_id)
                    digest = (
                        _source_hash(self.store.project_directory(project_id))
                        if project.state == ProjectState.RUNNING
                        else None
                    )
                except (OSError, ValueError):
                    digest = None
                if digest is not None:
                    token: CancellationToken | None = None
                    changed = False
                    with self._condition:
                        if self._active_project_id == project_id and digest != self._observed_hash:
                            self._observed_hash = digest
                            self._pending_hash = digest
                            self._due_at = time.monotonic() + self.debounce_seconds
                            if self._running_hash is not None and self._running_hash != digest:
                                token = self._running_token
                            changed = True
                            self._condition.notify_all()
                    if token is not None:
                        token.cancel()
                    if changed:
                        self._set_status(project_id, "stale", source_hash=digest)
            time.sleep(self.poll_seconds)

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while True:
                    if self._closed:
                        return
                    ready = (
                        self._active_project_id is not None
                        and self._pending_hash is not None
                        and self._due_at is not None
                        and not self._validating
                        and not self._user_paused
                    )
                    if ready:
                        remaining = self._due_at - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(timeout=remaining)
                    else:
                        self._condition.wait()
                project_id = self._active_project_id
                source_hash = self._pending_hash
                self._pending_hash = None
                self._due_at = None
                token = CancellationToken()
                self._running_hash = source_hash
                self._running_token = token
            if project_id is None or source_hash is None:
                continue
            self._set_status(project_id, "building", source_hash=source_hash)
            result = self.executor.execute(
                self.store.project_directory(project_id),
                cancellation_token=token,
            )
            try:
                disk_hash = _source_hash(self.store.project_directory(project_id))
            except OSError:
                disk_hash = None
            with self._condition:
                current = (
                    not self._closed
                    and self._active_project_id == project_id
                    and self._observed_hash == source_hash
                    and disk_hash == source_hash
                    and not self._validating
                    and not self._user_paused
                )
                if self._running_token is token:
                    self._running_token = None
                    self._running_hash = None
            if not current or result.status == "cancelled":
                continue
            store = LivePreviewStore(self.store.project_directory(project_id))
            if result.status == "succeeded" and result.payload is not None:
                try:
                    status = store.publish(result.payload, source_hash)
                except (OSError, PreviewError) as exc:
                    status = store.write_status("failed", source_hash=source_hash, error=str(exc))
            else:
                status = store.write_status(
                    "failed",
                    source_hash=source_hash,
                    error=result.error or "Live preview failed",
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            self._emit(project_id, status)

    def _set_status(
        self, project_id: str, state: str, *, source_hash: str | None = None
    ) -> LivePreviewStatus:
        store = LivePreviewStore(self.store.project_directory(project_id))
        status = store.write_status(state, source_hash=source_hash)
        self._emit(project_id, status)
        return status

    def _publish_validation_status(self, project_id: str) -> None:
        try:
            with self._condition:
                if self._active_project_id != project_id or not self._validating:
                    return
                self._set_status(project_id, "validating")
        except Exception:
            return

    def _emit(self, project_id: str, status: LivePreviewStatus) -> None:
        if self.on_status is None:
            return
        try:
            self.on_status(project_id, status)
        except Exception:
            return


def _source_hash(project_dir: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        path
        for path in project_dir.rglob("*.py")
        if path.is_file()
        and not path.is_symlink()
        and not any(part in _EXCLUDED_ROOT_NAMES for part in path.relative_to(project_dir).parts)
    )
    for path in paths:
        relative = path.relative_to(project_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _copy_preview_inputs(source: Path, destination: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        root = Path(directory)
        ignored: set[str] = set()
        for name in names:
            candidate = root / name
            if name in _EXCLUDED_ROOT_NAMES or name in _EXCLUDED_FILES:
                ignored.add(name)
            elif name.startswith(".env") or candidate.is_symlink():
                ignored.add(name)
        return ignored

    shutil.copytree(source, destination, ignore=ignore)


def _lower_process_priority() -> None:
    try:
        os.nice(10)
    except OSError:
        return


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            return
    except (OSError, ProcessLookupError):
        return


def _safe_output(payload: bytes) -> str:
    text = payload[-PREVIEW_OUTPUT_BYTES:].decode("utf-8", errors="replace")
    return redact_credentials(text).strip()


def _first_error(stderr: str, stdout: str) -> str:
    for value in (stderr, stdout):
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if lines:
            return redact_credentials(lines[-1])[:500]
    return "Live preview process exited with a non-zero status"


def _bounded(value: str | None) -> str | None:
    if value is None:
        return None
    clean = redact_credentials(value).strip()
    return clean[-PREVIEW_OUTPUT_BYTES:] or None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


_PREVIEW_RUNNER = r'''
import runpy
import sys
from pathlib import Path

import cadflow as cad

model_path = Path(sys.argv[1]).resolve()
output_path = Path(sys.argv[2]).resolve()
namespace = runpy.run_path(str(model_path), run_name="cadflow_live_preview")
build_model = namespace.get("build_model")
if not callable(build_model):
    raise RuntimeError("Model Source must define build_model(model) -> cad.Shape")
with cad.Model() as model:
    final_shape = build_model(model)
    if not isinstance(final_shape, cad.Shape):
        raise TypeError("Model Source must return one CadFlow Shape")
    topology = final_shape.topology
    if int(topology.get("solids", 0)) < 1:
        raise ValueError("Live preview Shape must contain at least one solid")
    payload = final_shape.preview_glb(deflection=0.35)
    cad.scene.preflight_glb(payload, expected_kind="triangle")
    output_path.write_bytes(payload)
'''


__all__ = [
    "LIVE_PREVIEW_MODEL_NAME",
    "LIVE_PREVIEW_STATUS_NAME",
    "LivePreviewExecutor",
    "LivePreviewResult",
    "LivePreviewScheduler",
    "LivePreviewStatus",
    "LivePreviewStore",
]
