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
_RESULT_PREFIX = "__CADFLOW_EXECUTION_RESULT__"
_PREFLIGHT_PREFIX = "__CADFLOW_PREFLIGHT__"

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
_TRACEBACK_LOCATION = re.compile(r'File "([^"]+)", line (\d+)')
_ARTIFACT_VERSION_DIRECTORY = re.compile(r"^v[0-9]{4,}$")


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
    final_shape_count: int | None
    solid_count: int | None
    solid_volume: float | None
    scene_artifact_exists: bool
    scene_parse_result: SceneParseResult
    artifact_entries: tuple[str, ...]
    duration_seconds: float
    process_id: int | None = None
    error_type: str | None = None
    error_location: str | None = None
    preflight_status: str = "not_run"
    imported_modules: tuple[str, ...] = ()
    review_artifact_dir: str | None = None
    review_manifest_path: str | None = None
    review_model_sha256: str | None = None
    review_evidence_error: str | None = None
    result_kind: str | None = None
    component_count: int | None = None
    leaf_part_count: int | None = None

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
            "final_shape_count": self.final_shape_count,
            "solid_count": self.solid_count,
            "solid_volume": self.solid_volume,
            "scene_artifact_exists": self.scene_artifact_exists,
            "scene_parse_result": self.scene_parse_result.to_dict(),
            "artifact_entries": list(self.artifact_entries),
            "duration_seconds": self.duration_seconds,
            "process_id": self.process_id,
            "error_type": self.error_type,
            "error_location": self.error_location,
            "preflight_status": self.preflight_status,
            "imported_modules": list(self.imported_modules),
            "review_artifact_dir": self.review_artifact_dir,
            "review_manifest_path": self.review_manifest_path,
            "review_model_sha256": self.review_model_sha256,
            "review_evidence_error": self.review_evidence_error,
            "result_kind": self.result_kind,
            "component_count": self.component_count,
            "leaf_part_count": self.leaf_part_count,
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
    def __init__(
        self,
        limit: int,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self.limit = max(0, limit)
        self.payload = bytearray()
        self.total_bytes = 0
        self._on_line = on_line
        self._line_buffer = bytearray()

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
            if self._on_line is not None:
                self._consume_lines(chunk)
        if self._on_line is not None and self._line_buffer:
            self._emit_line(bytes(self._line_buffer))
            self._line_buffer.clear()

    def _consume_lines(self, chunk: bytes) -> None:
        self._line_buffer.extend(chunk)
        while True:
            try:
                newline = self._line_buffer.index(b"\n")
            except ValueError:
                # Control messages are short. Drop an unterminated noisy line
                # rather than allowing arbitrary model stdout to grow forever.
                if len(self._line_buffer) > 1_048_576:
                    self._line_buffer.clear()
                return
            line = bytes(self._line_buffer[:newline]).rstrip(b"\r")
            del self._line_buffer[: newline + 1]
            self._emit_line(line)

    def _emit_line(self, line: bytes) -> None:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return
        try:
            self._on_line(text) if self._on_line is not None else None
        except Exception:
            # Preview delivery is observability; it must not change CAD status.
            return


class CADExecutor:
    """Run one Model Source with operational limits, not source isolation."""

    def execute(
        self,
        project_dir: str | Path,
        *,
        timeout_seconds: float = CAD_EXECUTION_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_OUTPUT_BYTES,
        cancellation_token: object | None = None,
        attempt: int = 1,
    ) -> ExecutionResult:
        """Execute ``model.py`` in the Project's working directory.

        The source is intentionally executed without policy-based AST, import,
        or dangerous-call inspection. The compile/import preflight only reports
        source errors; it does not reject an API based on its origin. This is
        the trusted-local-demo decision in ADR-0004; the subprocess timeout,
        output bound, environment filtering, and process termination are
        operational controls only.
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
        preflight_error: str | None = None
        preflight_location: str | None = None
        preflight_status = "not_run"
        imported_modules: tuple[str, ...] = ()
        stdout_collector = _OutputCollector(max_output_bytes)
        stderr_collector = _OutputCollector(max_output_bytes)

        try:
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise ValueError("timeout_seconds must be finite and positive")
            if max_output_bytes < 0:
                raise ValueError("max_output_bytes must not be negative")
            if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
                raise ValueError("attempt must be a positive integer")
            self._validate_project_paths(root, model_path, artifact_dir)
            self._clear_artifacts(artifact_dir)
            if _is_cancelled(cancellation_token):
                forced_status = "cancelled"
            else:
                preflight_error, preflight_location = _preflight_source(model_path)
                if preflight_error is None:
                    preflight_status = "passed"
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
                else:
                    preflight_status = "failed"
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
        preflight_payload = _runner_preflight_payload(bytes(stdout_collector.payload))
        if preflight_payload is not None:
            imported_modules = _module_names(preflight_payload)
        elif (
            preflight_status == "passed"
            and exit_code != 0
            and _looks_like_import_error(stderr, stdout)
        ):
            preflight_status = "failed"
        shape_count, solid_count, solid_volume = _shape_facts(payload)
        result_kind = _result_kind(payload)
        component_count, leaf_part_count = _assembly_facts(payload)
        (
            review_artifact_dir,
            review_manifest_path,
            review_model_sha256,
            review_evidence_error,
        ) = _review_facts(payload)

        status = "succeeded"
        error: str | None = None
        error_type: str | None = None
        error_location: str | None = None
        if preflight_error is not None:
            status = "failed"
            error = redact_credentials(preflight_error)
            error_type = "syntax"
            error_location = preflight_location
        elif launch_error is not None:
            status = "failed"
            error = redact_credentials(launch_error)
            error_type = "preflight"
        elif forced_status is not None:
            status = forced_status
            error = (
                "CAD execution cancelled by caller"
                if forced_status == "cancelled"
                else f"CAD execution timed out after {timeout_seconds:g} seconds"
            )
            error_type = forced_status
        elif exit_code != 0:
            status = "failed"
            error = _first_error(
                stderr, stdout, "CAD process exited with a non-zero status"
            )
            error_type = _classify_process_error(stderr, stdout)
            error_location = _traceback_location(stderr, stdout)
        elif payload is None:
            status = "failed"
            error = "CAD process did not report a CadFlow Model Source result"
            error_type = "execution"
        elif result_kind not in {"part", "assembly"}:
            status = "failed"
            error = "CAD process reported an unknown Model Source result type"
            error_type = "topology"
        elif shape_count != 1:
            status = "failed"
            error = f"Model Source returned {shape_count or 0} results; expected exactly one"
            error_type = "topology"
        elif result_kind == "part" and solid_count != 1:
            status = "failed"
            error = (
                f"Model Source cad.Shape contains {solid_count or 0} solids; "
                "multi-part models must return cad.Assembly"
                if solid_count is not None and solid_count > 1
                else "final Shape must be solid-compatible and contain exactly one solid"
            )
            error_type = "topology"
        elif result_kind == "assembly" and (
            component_count is None
            or component_count < 1
            or leaf_part_count is None
            or leaf_part_count < 1
        ):
            status = "failed"
            error = "Model Source Assembly must contain at least one leaf Part"
            error_type = "topology"
        elif result_kind == "assembly" and solid_count != leaf_part_count:
            status = "failed"
            error = "every Assembly leaf Part must contain exactly one solid"
            error_type = "topology"
        elif (
            solid_volume is None or not math.isfinite(solid_volume) or solid_volume <= 0
        ):
            status = "failed"
            error = "final Shape volume must be finite and greater than zero"
            error_type = "geometry"
        elif _artifact_has_symlink(artifact_dir):
            status = "failed"
            error = "artifacts must not contain symbolic links"
            error_type = "scene"
        elif artifact_entries != (SCENE_ARTIFACT_NAME,):
            status = "failed"
            error = "artifacts must contain only artifacts/model.scene.zip"
            error_type = "scene"
        elif not scene_parse.valid:
            status = "failed"
            error = scene_parse.error or "canonical Scene Artifact could not be parsed"
            error_type = "scene"
        elif review_evidence_error is not None:
            status = "failed"
            error = f"CAD review evidence could not be generated: {review_evidence_error}"
            error_type = "review_evidence"
        elif not review_manifest_path or not review_artifact_dir:
            status = "failed"
            error = "CAD review evidence was not generated"
            error_type = "review_evidence"
        if error_location is None and error_type in {"syntax", "import", "api"}:
            error_location = _traceback_location(stderr, stdout)

        return ExecutionResult(
            status=status,
            exit_code=exit_code,
            error=error,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_was_truncated,
            stderr_truncated=stderr_was_truncated,
            final_shape_count=shape_count,
            solid_count=solid_count,
            solid_volume=solid_volume,
            scene_artifact_exists=scene_exists,
            scene_parse_result=scene_parse,
            artifact_entries=artifact_entries,
            duration_seconds=duration,
            process_id=process.pid if process is not None else None,
            error_type=error_type,
            error_location=error_location,
            preflight_status=preflight_status,
            imported_modules=imported_modules,
            review_artifact_dir=review_artifact_dir,
            review_manifest_path=review_manifest_path,
            review_model_sha256=review_model_sha256,
            review_evidence_error=review_evidence_error,
            result_kind=result_kind,
            component_count=component_count,
            leaf_part_count=leaf_part_count,
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
            if child.is_dir() and _ARTIFACT_VERSION_DIRECTORY.fullmatch(child.name):
                continue
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


def _runner_preflight_payload(raw_stdout: bytes) -> dict[str, object] | None:
    text = raw_stdout.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith(_PREFLIGHT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_PREFLIGHT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _preflight_source(model_path: Path) -> tuple[str | None, str | None]:
    """Compile Model Source without executing it or creating a pyc file."""

    try:
        source = model_path.read_text(encoding="utf-8")
        compile(source, str(model_path), "exec")
    except SyntaxError as exc:
        location = f"{model_path}:{exc.lineno or 0}:{exc.offset or 0}"
        return f"SyntaxError: {exc.msg}", location
    except (OSError, UnicodeError) as exc:
        return str(exc), str(model_path)
    return None, None


def _module_names(payload: dict[str, object]) -> tuple[str, ...]:
    values = payload.get("imported_modules")
    if not isinstance(values, list):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _looks_like_import_error(stderr: str, stdout: str) -> bool:
    text = f"{stderr}\n{stdout}"
    return "ModuleNotFoundError" in text or "ImportError" in text


def _classify_process_error(stderr: str, stdout: str) -> str:
    text = f"{stderr}\n{stdout}"
    if "SyntaxError" in text:
        return "syntax"
    if "ModuleNotFoundError" in text or "ImportError" in text:
        return "import"
    if any(
        marker in text
        for marker in (
            "TypeError",
            "AttributeError",
            "NameError",
            "NotImplementedError",
        )
    ):
        return "api"
    return "execution"


def _traceback_location(stderr: str, stdout: str) -> str | None:
    matches = _TRACEBACK_LOCATION.findall(f"{stderr}\n{stdout}")
    if not matches:
        return None
    path, line = matches[-1]
    return f"{path}:{line}"


def _shape_facts(
    payload: dict[str, object] | None,
) -> tuple[int | None, int | None, float | None]:
    if payload is None:
        return None, None, None
    count = payload.get("final_shape_count")
    solid_count = payload.get("solid_count")
    volume = payload.get("solid_volume")
    if not isinstance(count, int) or isinstance(count, bool):
        return None, None, None
    if not isinstance(solid_count, int) or isinstance(solid_count, bool):
        solid_count = None
    if not isinstance(volume, (int, float)) or isinstance(volume, bool):
        volume = None
    return count, solid_count, float(volume) if volume is not None else None


def _result_kind(payload: dict[str, object] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("result_kind")
    return value if value in {"part", "assembly"} else None


def _assembly_facts(
    payload: dict[str, object] | None,
) -> tuple[int | None, int | None]:
    if payload is None:
        return None, None
    values = []
    for name in ("component_count", "leaf_part_count"):
        value = payload.get(name)
        values.append(
            value if isinstance(value, int) and not isinstance(value, bool) else None
        )
    return values[0], values[1]


def _review_facts(
    payload: dict[str, object] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Read bounded review evidence metadata emitted by the CAD child."""

    if payload is None:
        return None, None, None, None
    values = tuple(
        payload.get(name)
        for name in (
            "review_artifact_dir",
            "review_manifest_path",
            "review_model_sha256",
            "review_evidence_error",
        )
    )
    return tuple(
        value if isinstance(value, str) else None for value in values
    )  # type: ignore[return-value]


def _first_error(stderr: str, stdout: str, fallback: str) -> str:
    for candidate in (stderr.strip(), stdout.strip()):
        if candidate:
            return candidate
    return fallback


def _artifact_entries(artifact_dir: Path) -> tuple[str, ...]:
    if not artifact_dir.is_dir():
        return ()
    entries = []
    for path in artifact_dir.rglob("*"):
        relative = path.relative_to(artifact_dir)
        if relative.parts and _ARTIFACT_VERSION_DIRECTORY.fullmatch(relative.parts[0]):
            continue
        entries.append(relative.as_posix())
    return tuple(sorted(entries))


def _artifact_has_symlink(artifact_dir: Path) -> bool:
    if artifact_dir.is_symlink():
        return True
    for path in artifact_dir.rglob("*"):
        relative = path.relative_to(artifact_dir)
        if relative.parts and _ARTIFACT_VERSION_DIRECTORY.fullmatch(relative.parts[0]):
            continue
        if path.is_symlink():
            return True
    return False


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
    except PermissionError:
        # A short-lived macOS process-group race can make killpg unavailable;
        # still terminate the child itself so callers receive a result.
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


_RUNNER_SOURCE = f"""\
import builtins
import hashlib
import json
import math
import os
import runpy
import sys
import tempfile
from pathlib import Path

import cadflow as cad


SOURCE_EXCLUDED_ROOTS = {{
    ".cad-review",
    ".git",
    "__pycache__",
    "artifacts",
    "conversation_history",
    "large_tool_results",
    "previews",
}}


def source_manifest(project_root):
    digest = hashlib.sha256()
    records = []
    paths = sorted(
        path
        for path in project_root.rglob("*.py")
        if path.is_file()
        and not path.is_symlink()
        and not any(
            part in SOURCE_EXCLUDED_ROOTS
            for part in path.relative_to(project_root).parts
        )
    )
    for path in paths:
        relative_text = path.relative_to(project_root).as_posix()
        relative = relative_text.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        records.append({{
            "path": relative_text,
            "sha256": hashlib.sha256(content).hexdigest(),
        }})
    return digest.hexdigest(), records


def assembly_facts(root):
    component_count = 0
    leaf_part_count = 0
    solid_count = 0
    solid_volume = 0.0

    def visit(assembly, ancestors):
        nonlocal component_count, leaf_part_count, solid_count, solid_volume
        identity = id(assembly)
        if identity in ancestors:
            raise ValueError("Assembly component graph must not contain cycles")
        if not assembly.components:
            raise ValueError("Assembly and nested subassemblies must contain components")
        next_ancestors = ancestors | {{identity}}
        component_ids = set()
        for component in assembly.components:
            if component.component_id in component_ids:
                raise ValueError(
                    "Assembly component IDs must be unique within their parent"
                )
            component_ids.add(component.component_id)
            placement = component.placement.to_dict()
            coordinates = [
                value
                for name in ("origin", "x_axis", "y_axis", "z_axis")
                for value in placement.get(name, ())
            ]
            if len(coordinates) != 12 or not all(
                math.isfinite(float(value)) for value in coordinates
            ):
                raise ValueError("Assembly component placements must be finite")
            component_count += 1
            if isinstance(component.item, cad.Part):
                if not isinstance(component.item.body, cad.Solid):
                    raise TypeError("every Assembly leaf Part must contain one cad.Solid")
                volume = float(component.item.body.get_volume())
                if not math.isfinite(volume) or volume <= 0.0:
                    raise ValueError("every Assembly leaf Part must have positive volume")
                leaf_part_count += 1
                solid_count += 1
                solid_volume += volume
            elif isinstance(component.item, cad.Assembly):
                visit(component.item, next_ancestors)
            else:
                raise TypeError("Assembly components must contain cad.Part or cad.Assembly")

    visit(root, set())
    return component_count, leaf_part_count, solid_count, solid_volume


model_path = Path(sys.argv[1]).resolve()
review_model_sha256, review_source_files = source_manifest(model_path.parent)
imported_modules = {{"cadflow"}}
real_import = builtins.__import__


def tracking_import(name, globals=None, locals=None, fromlist=(), level=0):
    imported_modules.add(name)
    return real_import(name, globals, locals, fromlist, level)


builtins.__import__ = tracking_import
try:
    namespace = runpy.run_path(str(model_path), run_name="cadflow_model_source")
finally:
    builtins.__import__ = real_import
build_model = namespace.get("build_model")
if not callable(build_model):
    raise RuntimeError(
        "Model Source must define build_model(model) -> cad.Shape | cad.Assembly"
    )

print({_PREFLIGHT_PREFIX!r} + json.dumps(
    {{"status": "passed", "imported_modules": sorted(imported_modules)[:256]}},
    separators=(",", ":"),
), flush=True)

result_kind = None
final_shape_count = 1
component_count = None
leaf_part_count = None
solid_count = None
solid_volume = None
review_artifact_dir = None
review_manifest_path = None
review_evidence_error = None

with cad.Model() as model:
    final_result = build_model(model)
    artifact_dir = model_path.parent / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".cadflow-bridge-", dir=model_path.parent
    ) as bridge_dir:
        step_path = Path(bridge_dir) / "model.step"
        topology = {{}}
        if isinstance(final_result, cad.Shape):
            result_kind = "part"
            topology = dict(final_result.topology)
            solid_count = int(topology.get("solids", 0))
            solid_volume = float(final_result.volume)
            if solid_count == 1 and math.isfinite(solid_volume) and solid_volume > 0.0:
                final_result.export_step(str(step_path))
                ocp_shape = cad.inspection.brep.load_step_rshape(step_path)
                compat_solid = cad.Solid(ocp_shape)
                package = cad.compile_scene(
                    scene_id="model",
                    roots=(cad.SceneRoot(root_id="main", value=compat_solid),),
                    source=cad.SceneSource(kind="manual", source_id="model.py"),
                )
                cad.export_scene(
                    package=package,
                    path=artifact_dir / "model.scene.zip",
                )
        elif isinstance(final_result, cad.Assembly):
            result_kind = "assembly"
            (
                component_count,
                leaf_part_count,
                solid_count,
                solid_volume,
            ) = assembly_facts(final_result)
            if leaf_part_count >= 1 and solid_volume > 0.0:
                preview = cad.make_compound_from_assembly_rcompound(
                    assembly=final_result
                )
                cad.export_step(shapes=preview, filename=str(step_path))
                package = cad.compile_scene(
                    scene_id="model",
                    roots=(cad.SceneRoot(root_id="main", value=final_result),),
                    source=cad.SceneSource(kind="manual", source_id="model.py"),
                )
                cad.export_scene(
                    package=package,
                    path=artifact_dir / "model.scene.zip",
                )
        else:
            raise TypeError(
                "Model Source must return one CadFlow Shape or cad.Assembly"
            )

        if step_path.is_file():
            try:
                from cadflow.inspect import brep

                inspection = brep.inspect_step_rbrepinspection(step_path)
                review_root = model_path.parent / ".cad-review" / review_model_sha256
                review_root.mkdir(parents=True, exist_ok=True)
                single_render_path = review_root / "isometric.png"
                contact_sheet_path = review_root / "contact-sheet.png"
                common_render_options = {{
                    "image_size": (8.0, 8.0),
                    "dpi": 64,
                    "background_color": (0.965, 0.972, 0.980),
                    "show_brep_edges": True,
                }}
                brep.render_step_views_rpath(
                    step_path,
                    single_render_path,
                    views=((35.0, -45.0, "isometric"),),
                    title="CAD Review - Isometric",
                    **common_render_options,
                )
                canonical_views = (
                    (0.0, 0.0, "front"),
                    (0.0, 180.0, "back"),
                    (0.0, 90.0, "right"),
                    (0.0, -90.0, "left"),
                    (90.0, 0.0, "top"),
                    (-90.0, 0.0, "bottom"),
                    (35.0, -45.0, "isometric"),
                    (35.0, 135.0, "isometric-rear"),
                )
                brep.render_step_views_rpath(
                    step_path,
                    contact_sheet_path,
                    views=canonical_views,
                    title="CAD Review - Eight Views",
                    **common_render_options,
                )

                def image_sha256(path):
                    return hashlib.sha256(path.read_bytes()).hexdigest()

                manifest = {{
                    "schema_version": "cad-review/v1",
                    "model_sha256": review_model_sha256,
                    "source_files": review_source_files,
                    "views": [
                        {{
                            "view_id": view[2],
                            "elevation": view[0],
                            "azimuth": view[1],
                        }}
                        for view in canonical_views
                    ],
                    "single_render": {{
                        "path": single_render_path.name,
                        "image_sha256": image_sha256(single_render_path),
                    }},
                    "contact_sheet": {{
                        "path": contact_sheet_path.name,
                        "image_sha256": image_sha256(contact_sheet_path),
                    }},
                    "metrics": {{
                        "result_kind": result_kind,
                        "component_count": component_count,
                        "leaf_part_count": leaf_part_count,
                        "solid_count": solid_count,
                        "volume_mm3": solid_volume,
                        "bbox_mm": [float(value) for value in inspection.bounding_box],
                        "topology": dict(inspection.counts),
                        "is_valid": (
                            solid_count == 1 and solid_volume > 0.0
                            if result_kind == "part"
                            else leaf_part_count is not None
                            and leaf_part_count >= 1
                            and solid_count == leaf_part_count
                            and solid_volume > 0.0
                        ),
                    }},
                }}
                manifest_path = review_root / "manifest.json"
                temporary_manifest = review_root / ".manifest.json.tmp"
                temporary_manifest.write_text(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(temporary_manifest, manifest_path)
                review_artifact_dir = str(review_root.relative_to(model_path.parent))
                review_manifest_path = str(
                    manifest_path.relative_to(model_path.parent)
                )
            except Exception as error:
                review_evidence_error = type(error).__name__ + ": " + str(error)

payload = {{
    "result_kind": result_kind,
    "final_shape_count": final_shape_count,
    "component_count": component_count,
    "leaf_part_count": leaf_part_count,
    "solid_count": solid_count,
    "solid_volume": solid_volume,
    "review_artifact_dir": review_artifact_dir,
    "review_manifest_path": review_manifest_path,
    "review_model_sha256": review_model_sha256,
    "review_evidence_error": review_evidence_error,
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
