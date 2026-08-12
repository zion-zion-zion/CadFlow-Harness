"""Execute Model Source at the trusted local CAD subprocess seam."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .model_source import (
    ARTIFACT_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    SCENE_ARTIFACT_NAME,
)
from .scene_validation import SceneParseResult, validate_scene_artifact


CAD_EXECUTION_TIMEOUT_SECONDS = 120.0
DEFAULT_OUTPUT_BYTES = 64 * 1024
_RESULT_PREFIX = "__SIMPLECAD_EXECUTION_RESULT__"

_SENSITIVE_ENV_NAME = re.compile(
    r"(?i)(api[_-]?(?:key|token)|access[_-]?token|secret|password|credential|"
    r"authorization|endpoint|base[_-]?url|openai|anthropic|gemini|langchain|"
    r"langsmith|cohere|mistral|groq|azure)"
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b([A-Za-z][A-Za-z0-9_.-]*(?:api[_-]?(?:key|token)|access[_-]?token|"
    r"secret|password|credential|authorization|endpoint|base[_-]?url))"
    r"\s*([=:])\s*([^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_TOKEN_SECRET = re.compile(
    r"\b(?:sk|pk|ghp|github_pat|xox[baprs])-[A-Za-z0-9._~+/=-]{8,}"
)


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
    captured_solid_count: int | None
    solid_volume: float | None
    scene_artifact_exists: bool
    scene_parse_result: SceneParseResult
    artifact_entries: tuple[str, ...]
    duration_seconds: float
    process_id: int | None = None

    @property
    def output_truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "captured_solid_count": self.captured_solid_count,
            "solid_volume": self.solid_volume,
            "scene_artifact_exists": self.scene_artifact_exists,
            "scene_parse_result": self.scene_parse_result.to_dict(),
            "artifact_entries": list(self.artifact_entries),
            "duration_seconds": self.duration_seconds,
            "process_id": self.process_id,
        }


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


class _OutputCollector:
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
                return
            self.total_bytes += len(chunk)
            if len(self.payload) < self.limit:
                remaining = self.limit - len(self.payload)
                self.payload.extend(chunk[:remaining])


class CADExecutor:
    """Run one Model Source with operational limits, not source isolation."""

    def execute(
        self,
        project_dir: str | Path,
        *,
        timeout_seconds: float = CAD_EXECUTION_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_OUTPUT_BYTES,
        cancellation_token: object | None = None,
    ) -> ExecutionResult:
        """Execute ``model.py`` in the Project's working directory.

        The source is intentionally executed without AST, import, or
        dangerous-call inspection. This is the trusted-local-demo decision in
        ADR-0004; the subprocess timeout, output bound, environment filtering,
        and process termination are operational controls only.
        """

        started = time.monotonic()
        root = Path(project_dir).expanduser().resolve()
        model_path = root / MODEL_SOURCE_NAME
        artifact_dir = root / ARTIFACT_DIRECTORY_NAME
        scene_path = artifact_dir / SCENE_ARTIFACT_NAME
        process: subprocess.Popen[bytes] | None = None
        exit_code: int | None = None
        forced_status: str | None = None
        launch_error: str | None = None
        stdout_collector = _OutputCollector(max_output_bytes)
        stderr_collector = _OutputCollector(max_output_bytes)

        try:
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise ValueError("timeout_seconds must be finite and positive")
            if max_output_bytes < 0:
                raise ValueError("max_output_bytes must not be negative")
            self._validate_project_paths(root, model_path, artifact_dir)
            self._clear_artifacts(artifact_dir)
            if _is_cancelled(cancellation_token):
                forced_status = "cancelled"
            else:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-u",
                        "-c",
                        _RUNNER_SOURCE,
                        str(model_path),
                    ],
                    cwd=root,
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
                        _terminate_process(process)
                        break
                    if time.monotonic() - started >= timeout_seconds:
                        forced_status = "timed_out"
                        _terminate_process(process)
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
                _terminate_process(process)
                exit_code = process.wait()
        finally:
            if process is not None:
                _clear_registered_process(cancellation_token, process)
            duration = time.monotonic() - started

        stdout, stdout_was_truncated = _safe_output(
            bytes(stdout_collector.payload),
            max_output_bytes,
            already_truncated=stdout_collector.truncated,
        )
        stderr, stderr_was_truncated = _safe_output(
            bytes(stderr_collector.payload),
            max_output_bytes,
            already_truncated=stderr_collector.truncated,
        )
        artifact_entries = _artifact_entries(artifact_dir)
        scene_exists = scene_path.is_file() and not scene_path.is_symlink()
        scene_parse = (
            validate_scene_artifact(scene_path)
            if scene_exists
            else SceneParseResult(
                valid=False, error="canonical Scene Artifact is missing"
            )
        )
        payload = _runner_payload(bytes(stdout_collector.payload))
        captured_count, solid_volume = _solid_facts(payload)

        status = "succeeded"
        error: str | None = None
        if launch_error is not None:
            status = "failed"
            error = redact_credentials(launch_error)
        elif forced_status is not None:
            status = forced_status
            error = (
                "CAD execution cancelled by caller"
                if forced_status == "cancelled"
                else f"CAD execution timed out after {timeout_seconds:g} seconds"
            )
        elif exit_code != 0:
            status = "failed"
            error = _first_error(
                stderr, stdout, "CAD process exited with a non-zero status"
            )
        elif payload is None:
            status = "failed"
            error = "CAD process did not report a captured Model Source result"
        elif captured_count != 1:
            status = "failed"
            error = f"Model Source captured {captured_count or 0} Solids; expected exactly one"
        elif (
            solid_volume is None or not math.isfinite(solid_volume) or solid_volume <= 0
        ):
            status = "failed"
            error = "captured Solid volume must be finite and greater than zero"
        elif _artifact_has_symlink(artifact_dir):
            status = "failed"
            error = "artifacts must not contain symbolic links"
        elif artifact_entries != (SCENE_ARTIFACT_NAME,):
            status = "failed"
            error = "artifacts must contain only artifacts/model.scene.zip"
        elif not scene_parse.valid:
            status = "failed"
            error = scene_parse.error or "canonical Scene Artifact could not be parsed"

        return ExecutionResult(
            status=status,
            exit_code=exit_code,
            error=error,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_was_truncated,
            stderr_truncated=stderr_was_truncated,
            captured_solid_count=captured_count,
            solid_volume=solid_volume,
            scene_artifact_exists=scene_exists,
            scene_parse_result=scene_parse,
            artifact_entries=artifact_entries,
            duration_seconds=duration,
            process_id=process.pid if process is not None else None,
        )

    @staticmethod
    def _validate_project_paths(
        root: Path, model_path: Path, artifact_dir: Path
    ) -> None:
        if not root.is_dir():
            raise ValueError("Project working directory does not exist")
        if model_path.is_symlink():
            raise ValueError("Model Source must not be a symlink")
        if not _is_under(model_path.resolve(), root):
            raise ValueError("Model Source is outside the Project")
        if not model_path.is_file():
            raise ValueError("current Project Model Source is missing")
        if artifact_dir.is_symlink():
            raise ValueError("Project artifact directory must not be a symlink")
        artifact_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _clear_artifacts(artifact_dir: Path) -> None:
        for child in artifact_dir.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()


def build_cad_environment(
    base_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy runtime environment while removing model-provider credentials."""

    environment = dict(os.environ if base_environment is None else base_environment)
    for name in tuple(environment):
        if _SENSITIVE_ENV_NAME.search(name):
            environment.pop(name, None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def redact_credentials(text: str) -> str:
    """Redact common key/token/endpoint forms from bounded diagnostics."""

    redacted = _ASSIGNMENT_SECRET.sub(r"\1\2[REDACTED]", text)
    redacted = _BEARER_SECRET.sub(r"\1 [REDACTED]", redacted)
    return _TOKEN_SECRET.sub("[REDACTED]", redacted)


def _safe_output(
    raw: bytes,
    limit: int,
    *,
    already_truncated: bool,
) -> tuple[str, bool]:
    redacted = redact_credentials(raw.decode("utf-8", errors="replace"))
    encoded = redacted.encode("utf-8")
    truncated = already_truncated or len(encoded) > limit
    if len(encoded) > limit:
        encoded = encoded[:limit]
        redacted = encoded.decode("utf-8", errors="ignore")
    return redacted, truncated


def _runner_payload(raw_stdout: bytes) -> dict[str, object] | None:
    text = raw_stdout.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if not line.startswith(_RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _solid_facts(payload: dict[str, object] | None) -> tuple[int | None, float | None]:
    if payload is None:
        return None, None
    count = payload.get("captured_solid_count")
    volumes = payload.get("solid_volumes")
    if not isinstance(count, int) or isinstance(count, bool):
        return None, None
    volume = None
    if count == 1 and isinstance(volumes, list) and len(volumes) == 1:
        candidate = volumes[0]
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            volume = float(candidate)
    return count, volume


def _first_error(stderr: str, stdout: str, fallback: str) -> str:
    for candidate in (stderr.strip(), stdout.strip()):
        if candidate:
            return candidate
    return fallback


def _artifact_entries(artifact_dir: Path) -> tuple[str, ...]:
    if not artifact_dir.is_dir():
        return ()
    entries = [
        path.relative_to(artifact_dir).as_posix() for path in artifact_dir.rglob("*")
    ]
    return tuple(sorted(entries))


def _artifact_has_symlink(artifact_dir: Path) -> bool:
    return artifact_dir.is_symlink() or any(
        path.is_symlink() for path in artifact_dir.rglob("*")
    )


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_cancelled(token: object | None) -> bool:
    if token is None:
        return False
    if isinstance(token, threading.Event):
        return token.is_set()
    cancelled = getattr(token, "cancelled", False)
    return bool(cancelled() if callable(cancelled) else cancelled)


def _register_process(token: object | None, process: subprocess.Popen[bytes]) -> None:
    register = getattr(token, "register_process", None)
    if callable(register):
        register(process, _terminate_process)


def _clear_registered_process(
    token: object | None, process: subprocess.Popen[bytes]
) -> None:
    clear = getattr(token, "clear_process", None)
    if callable(clear):
        clear(process)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
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
        process.wait()


_RUNNER_SOURCE = f"""\
import json
import runpy
import sys

import simplecadapi as scad


namespace = runpy.run_path(sys.argv[1], run_name="__main__")
model_result = namespace.get("MODEL_RESULT")
if model_result is None:
    raise RuntimeError("Model Source must define MODEL_RESULT from its @scad.model entry point")


solids = []


def collect(value):
    if isinstance(value, scad.Solid):
        solids.append(value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            collect(item)


for captured in model_result.session.captured_values:
    collect(captured)

payload = {{
    "captured_solid_count": len(solids),
    "solid_volumes": [float(solid.get_volume()) for solid in solids],
}}
print({_RESULT_PREFIX!r} + json.dumps(payload, separators=(",", ":")), flush=True)
"""


__all__ = [
    "CAD_EXECUTION_TIMEOUT_SECONDS",
    "CADExecutor",
    "CancellationToken",
    "DEFAULT_OUTPUT_BYTES",
    "ExecutionResult",
    "build_cad_environment",
    "redact_credentials",
]
