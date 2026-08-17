"""Supervise the resident Pi worker and bridge its validator requests to Python."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .agent import (
    MAX_AGENT_RUN_SECONDS,
    AgentRunError,
    AgentRunOutcome,
    AgentSettings,
    _build_agent_system_prompt,
    _execution_failure_result,
    _is_validated_result,
    _safe_failure_reason,
)
from .agent_logging import AgentRunLog
from .cad_executor import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    CancellationToken,
    ExecutionResult,
    PreviewFrame,
    build_cad_environment,
    redact_credentials,
)
from .events import ProgressUpdate
from .harnesses import AgentHarness, HarnessUnavailableError
from .projects import ProjectState, ProjectStore
from .restricted_tools import AgentModelValidator


PI_IMPLEMENTATION_VERSION = "0.84.2"
PI_PROTOCOL_VERSION = 1
PI_STARTUP_TIMEOUT_SECONDS = 5.0
PI_ABORT_GRACE_SECONDS = 5.0
_TIMEOUT_REASON = "Agent Run exceeded the ten-minute wall-clock limit"


class PiProtocolError(AgentRunError):
    """Raised when the private worker protocol loses ordering or correlation."""


class PiWorkerSupervisor:
    """Own one restartable Node worker process for the FastAPI application."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        command: tuple[str, ...] | None = None,
        startup_timeout_seconds: float = PI_STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.command = command or (
            "node",
            str(self.repo_root / "pi-sidecar" / "dist" / "worker.js"),
        )
        self.startup_timeout_seconds = startup_timeout_seconds
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._generation = 0
        self._ready = threading.Event()
        self._frames: queue.Queue[dict[str, Any]] = queue.Queue()
        self._active_run_id: str | None = None
        self._shutting_down = False
        self._restart_scheduled = False
        self._last_error: str | None = None
        self._stderr: deque[str] = deque(maxlen=20)

    @property
    def available(self) -> bool:
        with self._lock:
            return self._is_alive_locked() and self._ready.is_set()

    @property
    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        with self._lock:
            return self._last_error or "Pi worker is unavailable"

    @property
    def process_id(self) -> int | None:
        with self._lock:
            return self._process.pid if self._is_alive_locked() else None

    def start(self) -> bool:
        with self._lock:
            if self._shutting_down:
                self._last_error = "Pi worker is shutting down"
                return False
            if not self._is_alive_locked():
                try:
                    self._launch_locked()
                except (OSError, ValueError) as exc:
                    self._last_error = _safe_worker_reason(exc)
                    return False
            ready = self._ready
            generation = self._generation
        if ready.wait(self.startup_timeout_seconds):
            return self.available
        with self._lock:
            if generation == self._generation:
                self._last_error = "Pi worker did not become ready before the startup deadline"
                self._terminate_locked()
        return False

    def begin_run(self, run_id: str, payload: Mapping[str, Any]) -> None:
        if not self.start():
            raise HarnessUnavailableError(self.unavailable_reason or "Pi worker is unavailable")
        with self._lock:
            if self._active_run_id is not None:
                raise PiProtocolError("Pi worker already has an active Run")
            self._drain_frames_locked()
            self._active_run_id = run_id
            try:
                self._send_locked("start_run", run_id, payload)
            except Exception:
                self._active_run_id = None
                raise

    def next_frame(self, run_id: str, timeout_seconds: float) -> dict[str, Any] | None:
        try:
            frame = self._frames.get(timeout=max(0.0, timeout_seconds))
        except queue.Empty:
            return None
        frame_run_id = frame.get("run_id")
        if frame.get("type") not in {"protocol_error", "process_exit"} and frame_run_id != run_id:
            raise PiProtocolError("Pi worker emitted a frame for an inactive Run")
        return frame

    def send_validator_result(
        self,
        run_id: str,
        correlation_id: str,
        result: Mapping[str, Any],
    ) -> None:
        with self._lock:
            self._require_active_locked(run_id)
            self._send_locked(
                "validator_result",
                run_id,
                result,
                correlation_id=correlation_id,
            )

    def abort(self, run_id: str) -> None:
        with self._lock:
            if self._active_run_id != run_id or not self._is_alive_locked():
                return
            self._send_locked("abort_run", run_id, {})

    def finish_run(self, run_id: str) -> None:
        with self._lock:
            if self._active_run_id == run_id:
                self._active_run_id = None
            if not self._is_alive_locked() and not self._shutting_down:
                self._schedule_restart_locked()

    def force_restart(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is not None and self._active_run_id not in {None, run_id}:
                return
            self._active_run_id = None
            self._terminate_locked()
        self.start()

    def shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True
            process = self._process
            if self._is_alive_locked():
                try:
                    self._send_locked("shutdown", "", {})
                except (BrokenPipeError, OSError, PiProtocolError):
                    pass
        if process is not None:
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        with self._lock:
            self._terminate_locked()

    def _launch_locked(self) -> None:
        worker_path = Path(self.command[-1]) if len(self.command) > 1 else None
        if worker_path is not None and worker_path.suffix == ".js" and not worker_path.is_file():
            raise OSError(
                "Pi worker build is missing; run npm ci && npm run build in pi-sidecar"
            )
        environment = build_cad_environment()
        environment.update(
            {
                "PI_OFFLINE": "1",
                "PI_TELEMETRY": "0",
                "PI_SKIP_VERSION_CHECK": "1",
            }
        )
        self._generation += 1
        generation = self._generation
        self._ready = threading.Event()
        self._last_error = None
        self._stderr.clear()
        process = subprocess.Popen(
            self.command,
            cwd=self.repo_root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            close_fds=True,
            start_new_session=os.name == "posix",
        )
        self._process = process
        threading.Thread(
            target=self._read_stdout,
            args=(process, generation),
            name="pi-worker-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(process, generation),
            name="pi-worker-stderr",
            daemon=True,
        ).start()

    def _read_stdout(self, process: subprocess.Popen[str], generation: int) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                try:
                    frame = _parse_worker_frame(line)
                except PiProtocolError as exc:
                    self._frames.put(_internal_frame("protocol_error", "", _safe_worker_reason(exc)))
                    continue
                if frame["type"] == "ready" and frame["run_id"] == "":
                    with self._lock:
                        if generation == self._generation:
                            self._ready.set()
                    continue
                self._frames.put(frame)
        finally:
            return_code = process.wait()
            with self._lock:
                if generation == self._generation:
                    self._ready.clear()
                    active_run_id = self._active_run_id
                    self._process = None
                    if active_run_id is not None:
                        self._frames.put(
                            _internal_frame(
                                "process_exit",
                                active_run_id,
                                f"Pi worker exited with status {return_code}",
                            )
                        )
                    elif not self._shutting_down:
                        self._last_error = f"Pi worker exited with status {return_code}"
                        self._schedule_restart_locked()

    def _read_stderr(self, process: subprocess.Popen[str], generation: int) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            safe = redact_credentials(line.strip())[:500]
            if not safe:
                continue
            with self._lock:
                if generation != self._generation:
                    return
                self._stderr.append(safe)
                self._last_error = f"Pi worker diagnostic: {safe}"

    def _schedule_restart_locked(self) -> None:
        if self._restart_scheduled or self._shutting_down:
            return
        self._restart_scheduled = True

        def restart() -> None:
            try:
                time.sleep(0.1)
                self.start()
            finally:
                with self._lock:
                    self._restart_scheduled = False

        threading.Thread(target=restart, name="pi-worker-restart", daemon=True).start()

    def _send_locked(
        self,
        message_type: str,
        run_id: str,
        payload: Mapping[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None:
        if not self._is_alive_locked() or self._process is None or self._process.stdin is None:
            raise HarnessUnavailableError("Pi worker is unavailable")
        frame = {
            "version": PI_PROTOCOL_VERSION,
            "run_id": run_id,
            "correlation_id": correlation_id,
            "type": message_type,
            "payload": dict(payload),
        }
        try:
            self._process.stdin.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise HarnessUnavailableError("Pi worker communication failed") from exc

    def _require_active_locked(self, run_id: str) -> None:
        if self._active_run_id != run_id:
            raise PiProtocolError("Pi worker frame is for an inactive Run")

    def _terminate_locked(self) -> None:
        process = self._process
        self._generation += 1
        self._ready.clear()
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=1.0)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except (OSError, ProcessLookupError):
                pass

    def _is_alive_locked(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _drain_frames_locked(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return


class PiAgentRunService:
    """Run Pi while Python retains validation, deadline, and outcome authority."""

    implementation_version = PI_IMPLEMENTATION_VERSION

    def __init__(
        self,
        *,
        store: ProjectStore,
        repo_root: str | Path,
        supervisor: PiWorkerSupervisor,
        settings_factory: Callable[[], AgentSettings] | None = None,
        executor: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.supervisor = supervisor
        self.settings_factory = settings_factory or AgentSettings.from_environment
        self.executor = executor
        self.clock = clock

    @property
    def available(self) -> bool:
        return self.supervisor.available

    @property
    def unavailable_reason(self) -> str | None:
        return self.supervisor.unavailable_reason

    def force_stop(self, project_id: str) -> None:
        self.supervisor.force_restart(project_id)

    def run(
        self,
        project_id: str,
        prompt: str,
        *,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
        prompt_submitted: bool = False,
        harness: AgentHarness = AgentHarness.PI,
    ) -> AgentRunOutcome:
        if AgentHarness(harness) is not AgentHarness.PI:
            raise AgentRunError("Pi run service received a non-Pi harness")
        if not self.available:
            raise HarnessUnavailableError(self.unavailable_reason or "Pi worker is unavailable")
        project = (
            self.store.get_project(project_id)
            if prompt_submitted
            else self.store.submit_prompt(project_id, prompt, AgentHarness.PI)
        )
        if project.state != ProjectState.RUNNING or project.harness is not AgentHarness.PI:
            raise AgentRunError("Pi Agent Run requires a Running Pi Project")
        token = cancellation_token or CancellationToken()
        started = self.clock()
        deadline = started + MAX_AGENT_RUN_SECONDS
        project_dir = self.store.project_directory(project_id)
        run_log = AgentRunLog(
            project_dir,
            harness=AgentHarness.PI.value,
            implementation_version=PI_IMPLEMENTATION_VERSION,
        )
        try:
            settings = self.settings_factory()
            run_log.configure(
                provider=settings.provider,
                model_id=settings.model_id,
                base_url=settings.base_url,
            )
        except Exception as exc:
            outcome = self._outcome(
                validated=False,
                failure_reason=_safe_failure_reason(exc),
                started=started,
            )
            run_log.finish(status=outcome.status, failure_reason=outcome.failure_reason)
            return outcome

        def emit(update: ProgressUpdate) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(update)
            except Exception:
                return

        def record_tool(record: Any) -> None:
            run_log.record_internal_tool(record.tool_name, record.target)

        validator = AgentModelValidator(
            repo_root=self.repo_root,
            project_dir=project_dir,
            executor=self.executor,
            on_tool_use=record_tool,
        )
        validator.begin_run()
        executions: list[ExecutionResult] = []
        payload = {
            "prompt": project.prompt or prompt,
            "project_dir": str(project_dir),
            "skill_root": str(self.repo_root / "skills"),
            "blocked_root": str(self.repo_root / "examples"),
            "system_prompt": _build_agent_system_prompt(
                workspace_root=project_dir,
                skill_root=self.repo_root / "skills",
            ),
            "provider": {
                "api_key": settings.api_key,
                "model_id": settings.model_id,
                "base_url": settings.base_url,
            },
        }
        try:
            self.supervisor.begin_run(project_id, payload)
        except Exception as exc:
            outcome = self._outcome(
                validated=False,
                failure_reason=_safe_pi_failure_reason(exc, settings.api_key),
                started=started,
            )
            run_log.finish(status=outcome.status, failure_reason=outcome.failure_reason)
            return outcome

        abort_sent_at: float | None = None
        outcome: AgentRunOutcome | None = None
        try:
            while outcome is None:
                now = self.clock()
                if now >= deadline and not token.cancelled:
                    token.cancel(reason="timeout")
                if token.cancelled and abort_sent_at is None:
                    abort_sent_at = now
                    self.supervisor.abort(project_id)
                if abort_sent_at is not None and now - abort_sent_at >= PI_ABORT_GRACE_SECONDS:
                    self.supervisor.force_restart(project_id)
                    outcome = self._cancelled_or_timed_out(token, executions, validator, started)
                    break
                frame = self.supervisor.next_frame(project_id, 0.05)
                if frame is None:
                    continue
                message_type = frame["type"]
                frame_payload = frame["payload"]
                if message_type == "run_started":
                    emit(ProgressUpdate(stage="reading_references", tool="pi"))
                elif message_type == "event":
                    self._record_event(frame_payload, run_log, emit)
                elif message_type == "validator_request":
                    correlation_id = frame.get("correlation_id")
                    if not isinstance(correlation_id, str) or not correlation_id:
                        raise PiProtocolError("validator request is missing a correlation ID")
                    result = self._validate(
                        validator,
                        executions,
                        token,
                        deadline,
                        emit,
                    )
                    self.supervisor.send_validator_result(
                        project_id,
                        correlation_id,
                        result.to_dict(),
                    )
                elif message_type == "complete":
                    usage = _pi_token_usage(frame_payload.get("token_usage"))
                    if usage is not None:
                        run_log.record_model_usage({"usage_metadata": usage})
                    outcome = self._completed_outcome(
                        executions,
                        validator,
                        started,
                        run_log.token_usage,
                    )
                elif message_type == "aborted":
                    outcome = self._cancelled_or_timed_out(token, executions, validator, started)
                elif message_type in {"failed", "protocol_error", "process_exit"}:
                    reason = frame_payload.get("reason")
                    if message_type in {"protocol_error", "process_exit"}:
                        self.supervisor.force_restart(project_id)
                    outcome = self._outcome(
                        validated=False,
                        failure_reason=(
                            _safe_pi_text(reason, settings.api_key)
                            if isinstance(reason, str) and reason.strip()
                            else "Pi worker failed"
                        ),
                        executions=executions,
                        validator=validator,
                        started=started,
                        cancelled=token.cancelled and token.cancellation_reason == "caller",
                        token_usage=run_log.token_usage,
                    )
                else:
                    raise PiProtocolError(f"unexpected Pi worker frame: {message_type}")
        except Exception as exc:
            self.supervisor.force_restart(project_id)
            outcome = self._outcome(
                validated=False,
                failure_reason=_safe_pi_failure_reason(exc, settings.api_key),
                executions=executions,
                validator=validator,
                started=started,
                cancelled=token.cancelled and token.cancellation_reason == "caller",
                token_usage=run_log.token_usage,
            )
        finally:
            self.supervisor.finish_run(project_id)

        if token.cancelled and token.cancellation_reason == "caller" and not outcome.cancelled:
            outcome = replace(
                outcome,
                validated=False,
                cancelled=True,
                failure_reason="Agent Run stopped by caller",
            )
        run_log.finish(status=outcome.status, failure_reason=outcome.failure_reason)
        return outcome

    def _validate(
        self,
        validator: AgentModelValidator,
        executions: list[ExecutionResult],
        token: CancellationToken,
        deadline: float,
        emit: Callable[[ProgressUpdate], None],
    ) -> ExecutionResult:
        attempt = len(executions) + 1
        remaining = deadline - self.clock()
        if remaining <= 0:
            token.cancel(reason="timeout")
            return _execution_failure_result(AgentRunError(_TIMEOUT_REASON))

        def preview(frame: PreviewFrame) -> None:
            emit(
                ProgressUpdate(
                    stage="preview_ready",
                    tool="cad",
                    attempt=attempt,
                    result=f"{frame.operation} preview",
                    preview_attempt=frame.attempt,
                    preview_revision=frame.revision,
                    preview_operation=frame.operation,
                )
            )

        try:
            result = validator.validate_model(
                cancellation_token=token,
                timeout_seconds=min(CAD_EXECUTION_TIMEOUT_SECONDS, remaining),
                attempt=attempt,
                preview_callback=preview,
            )
            if not isinstance(result, ExecutionResult):
                raise AgentRunError("validate_model returned an invalid structured result")
        except Exception as exc:
            result = _execution_failure_result(exc)
        executions.append(result)
        emit(
            ProgressUpdate(
                stage="executing",
                tool="cad",
                attempt=attempt,
                result=(
                    "Validated Result produced"
                    if _is_validated_result(result)
                    else f"CAD validation failed: {result.error or result.status}"
                ),
            )
        )
        return result

    @staticmethod
    def _record_event(
        payload: Mapping[str, Any],
        run_log: AgentRunLog,
        emit: Callable[[ProgressUpdate], None],
    ) -> None:
        event_type = payload.get("event_type")
        if not isinstance(event_type, str):
            return
        fields = {key: value for key, value in payload.items() if key != "event_type"}
        run_log.record_event(event_type, **fields)
        if event_type == "tool_call":
            tool_name = fields.get("tool_name")
            if tool_name == "read":
                emit(ProgressUpdate(stage="reading_references", tool="read"))
            elif tool_name in {"write", "edit"}:
                emit(ProgressUpdate(stage="writing_model", tool=str(tool_name)))
            elif tool_name == "write_todos":
                emit(ProgressUpdate(stage="planning", tool="write_todos"))

    def _completed_outcome(
        self,
        executions: list[ExecutionResult],
        validator: AgentModelValidator,
        started: float,
        token_usage: dict[str, int] | None,
    ) -> AgentRunOutcome:
        if not executions:
            return self._outcome(
                validated=False,
                failure_reason="Pi Agent finished without executing the Model Source",
                validator=validator,
                started=started,
                token_usage=token_usage,
            )
        final = executions[-1]
        if not _is_validated_result(final):
            return self._outcome(
                validated=False,
                failure_reason=final.error or "final Model Source did not produce a Validated Result",
                executions=executions,
                validator=validator,
                started=started,
                token_usage=token_usage,
            )
        return self._outcome(
            validated=True,
            executions=executions,
            validator=validator,
            started=started,
            token_usage=token_usage,
        )

    def _cancelled_or_timed_out(
        self,
        token: CancellationToken,
        executions: list[ExecutionResult],
        validator: AgentModelValidator,
        started: float,
    ) -> AgentRunOutcome:
        caller = token.cancellation_reason == "caller"
        return self._outcome(
            validated=False,
            failure_reason="Agent Run stopped by caller" if caller else _TIMEOUT_REASON,
            executions=executions,
            validator=validator,
            started=started,
            cancelled=caller,
        )

    def _outcome(
        self,
        *,
        validated: bool,
        failure_reason: str | None = None,
        executions: list[ExecutionResult] | None = None,
        validator: AgentModelValidator | None = None,
        started: float,
        cancelled: bool = False,
        token_usage: dict[str, int] | None = None,
    ) -> AgentRunOutcome:
        values = tuple(executions or ())
        return AgentRunOutcome(
            validated=validated,
            failure_reason=failure_reason,
            execution_results=values,
            tool_use_records=(validator.tool_use_records if validator is not None else ()),
            duration_seconds=max(0.0, self.clock() - started),
            token_usage=token_usage,
            cancelled=cancelled,
            harness=AgentHarness.PI,
            implementation_version=PI_IMPLEMENTATION_VERSION,
        )


def _parse_worker_frame(line: str) -> dict[str, Any]:
    try:
        frame = json.loads(line)
    except json.JSONDecodeError as exc:
        raise PiProtocolError("Pi worker emitted malformed JSON") from exc
    if not isinstance(frame, dict):
        raise PiProtocolError("Pi worker frame must be an object")
    if frame.get("version") != PI_PROTOCOL_VERSION:
        raise PiProtocolError("Pi worker protocol version is unsupported")
    if not isinstance(frame.get("run_id"), str):
        raise PiProtocolError("Pi worker frame has an invalid Run ID")
    correlation = frame.get("correlation_id")
    if correlation is not None and not isinstance(correlation, str):
        raise PiProtocolError("Pi worker frame has an invalid correlation ID")
    if not isinstance(frame.get("type"), str) or not frame["type"]:
        raise PiProtocolError("Pi worker frame has an invalid type")
    if not isinstance(frame.get("payload"), dict):
        raise PiProtocolError("Pi worker frame has an invalid payload")
    return frame


def _internal_frame(message_type: str, run_id: str, reason: str) -> dict[str, Any]:
    return {
        "version": PI_PROTOCOL_VERSION,
        "run_id": run_id,
        "correlation_id": None,
        "type": message_type,
        "payload": {"reason": reason},
    }


def _safe_worker_reason(error: Exception) -> str:
    return redact_credentials(str(error).strip().splitlines()[0])[:500] or type(error).__name__


def _safe_pi_failure_reason(error: Exception, api_key: str) -> str:
    return _safe_pi_text(str(error), api_key) or type(error).__name__


def _safe_pi_text(text: str, api_key: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    if api_key:
        first = first.replace(api_key, "[REDACTED]")
    return redact_credentials(first)[:500]


def _pi_token_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    cached_tokens = value.get("cached_input_tokens", 0)
    if not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0
        for item in (input_tokens, output_tokens, cached_tokens)
    ):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_token_details": {"cache_read": min(cached_tokens, input_tokens)},
    }


__all__ = [
    "PI_ABORT_GRACE_SECONDS",
    "PI_IMPLEMENTATION_VERSION",
    "PI_PROTOCOL_VERSION",
    "PiAgentRunService",
    "PiProtocolError",
    "PiWorkerSupervisor",
]
