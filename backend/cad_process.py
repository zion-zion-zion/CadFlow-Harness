"""Bounded CAD subprocess execution and cancellation lifecycle."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .cad_security import build_cad_environment


CAD_EXECUTION_TIMEOUT_SECONDS = 120.0
DEFAULT_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProcessExecution:
    """Raw bounded facts collected from one CAD child process."""

    exit_code: int | None
    forced_status: str | None
    launch_error: str | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    process_id: int | None


class CancellationToken:
    """Thread-safe cancellation signal shared with the active CAD process."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._reason: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._process_terminator: Callable[[subprocess.Popen[bytes]], None] | None = (
            None
        )

    def cancel(self, reason: str = "caller") -> None:
        if reason not in {"caller", "timeout"}:
            raise ValueError("cancellation reason must be caller or timeout")
        with self._lock:
            if self._reason is None:
                self._reason = reason
            self._event.set()
            process = self._process
            terminator = self._process_terminator
        if process is not None and terminator is not None:
            terminator(process)

    def register_process(
        self,
        process: subprocess.Popen[bytes],
        terminator: Callable[[subprocess.Popen[bytes]], None],
    ) -> None:
        """Bind the currently running CAD child so cancellation can terminate it."""

        with self._lock:
            self._process = process
            self._process_terminator = terminator
            cancelled = self._event.is_set()
        if cancelled:
            terminator(process)

    def clear_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            if self._process is process:
                self._process = None
                self._process_terminator = None

    @property
    def active_process_id(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process is not None else None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancellation_reason(self) -> str | None:
        with self._lock:
            return self._reason


class OutputCollector:
    """Read one pipe concurrently while retaining only a bounded prefix."""

    def __init__(self, limit: int) -> None:
        self.limit = max(0, limit)
        self.payload = bytearray()
        self.total_bytes = 0

    @property
    def truncated(self) -> bool:
        return self.total_bytes > self.limit

    def read_from(self, stream: object) -> None:
        reader = stream
        while True:
            chunk = reader.read(8192)  # type: ignore[attr-defined]
            if not chunk:
                break
            self.total_bytes += len(chunk)
            if len(self.payload) < self.limit:
                remaining = self.limit - len(self.payload)
                self.payload.extend(chunk[:remaining])


def execute_cad_process(
    *,
    model_path: Path,
    project_root: Path,
    code_root: Path,
    runner_path: Path,
    timeout_seconds: float,
    max_output_bytes: int,
    cancellation_token: object | None,
    started: float,
) -> ProcessExecution:
    """Start, supervise, and clean up the one-shot CAD runner."""

    process: subprocess.Popen[bytes] | None = None
    exit_code: int | None = None
    forced_status: str | None = None
    launch_error: str | None = None
    stdout_collector = OutputCollector(max_output_bytes)
    stderr_collector = OutputCollector(max_output_bytes)
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(runner_path),
                str(model_path),
                str(project_root),
                str(code_root),
            ],
            cwd=project_root,
            env=build_cad_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=os.name == "posix",
        )
        _register_process(cancellation_token, process)
        stdout_thread = threading.Thread(
            target=stdout_collector.read_from,
            args=(process.stdout,),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_collector.read_from,
            args=(process.stderr,),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        while process.poll() is None:
            if _is_cancelled(cancellation_token):
                forced_status = "cancelled"
                terminate_process(process)
                break
            if time.monotonic() - started >= timeout_seconds:
                forced_status = "timed_out"
                terminate_process(process)
                break
            time.sleep(0.01)
        exit_code = process.wait()
        if forced_status is None and _is_cancelled(cancellation_token):
            forced_status = "cancelled"
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
    except (OSError, ValueError) as exc:
        launch_error = str(exc)
        exit_code = process.poll() if process is not None else None
        if process is not None and process.poll() is None:
            terminate_process(process)
            exit_code = process.wait()
    finally:
        if process is not None:
            _clear_registered_process(cancellation_token, process)
    return ProcessExecution(
        exit_code=exit_code,
        forced_status=forced_status,
        launch_error=launch_error,
        stdout=bytes(stdout_collector.payload),
        stderr=bytes(stderr_collector.payload),
        stdout_truncated=stdout_collector.truncated,
        stderr_truncated=stderr_collector.truncated,
        process_id=process.pid if process is not None else None,
    )


def terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a child and its POSIX process group, escalating if needed."""

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            process.terminate()
        except (OSError, ProcessLookupError):
            return
    try:
        process.wait(timeout=0.75)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                process.kill()
            except (OSError, ProcessLookupError):
                return
        process.wait()


def _is_cancelled(token: object | None) -> bool:
    if token is None:
        return False
    if isinstance(token, threading.Event):
        return token.is_set()
    cancelled = getattr(token, "cancelled", False)
    return bool(cancelled() if callable(cancelled) else cancelled)


is_cancelled = _is_cancelled


def _register_process(token: object | None, process: subprocess.Popen[bytes]) -> None:
    register = getattr(token, "register_process", None)
    if callable(register):
        register(process, terminate_process)


def _clear_registered_process(
    token: object | None, process: subprocess.Popen[bytes]
) -> None:
    clear = getattr(token, "clear_process", None)
    if callable(clear):
        clear(process)


# Compatibility aliases for callers that used the old facade's private names.
_OutputCollector = OutputCollector
_terminate_process = terminate_process


__all__ = [
    "CAD_EXECUTION_TIMEOUT_SECONDS",
    "CancellationToken",
    "DEFAULT_OUTPUT_BYTES",
    "OutputCollector",
    "ProcessExecution",
    "execute_cad_process",
    "is_cancelled",
    "terminate_process",
]
