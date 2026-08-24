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
    create_model_source,
    project_code_directory,
)
from .product_artifact import (
    PRODUCT_ARTIFACT_MANIFEST_NAME,
    ProductArtifact,
    ProductArtifactError,
    load_product_artifact,
)
from .scene_validation import SceneParseResult, validate_scene_artifact


CAD_EXECUTION_TIMEOUT_SECONDS = 120.0
DEFAULT_OUTPUT_BYTES = 64 * 1024
_RESULT_PREFIX = "__CADFLOW_EXECUTION_RESULT__"
_PREFLIGHT_PREFIX = "__CADFLOW_PREFLIGHT__"
_PHASE_PREFIX = "__CADFLOW_EXECUTION_PHASE__"
_RUNNER_PATH = Path(__file__).with_name("cad_runner.py").resolve()

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
_VALIDATION_CHECK_LIMIT = 16
_VALIDATION_MAPPING_LIMIT = 32
_VALIDATION_TEXT_LIMIT = 1024
_VALIDATION_DEPTH_LIMIT = 8
_VALIDATION_NODE_LIMIT = 768
_VALIDATION_SEQUENCE_LIMIT = 32
_VALIDATION_SEQUENCE_LIMITS = {
    "contacts": 4,
    "failures": 12,
    "residuals": 24,
    "warnings": 8,
    "grounded_component_ids": 64,
    "solved_component_ids": 64,
    "unsolved_component_ids": 64,
}


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
    unique_part_count: int | None = None
    product_manifest_path: str | None = None
    product_status: str | None = None
    product_validation_status: str | None = None
    product_validation_failures: tuple[str, ...] = ()
    product_validation_checks: tuple[dict[str, object], ...] = ()
    execution_phase: str | None = None
    validation_short_circuited: bool = False

    @property
    def output_truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated

    @property
    def is_validated_product(self) -> bool:
        """Return whether this result may proceed to independent CAD review."""

        if self.result_kind == "part":
            structure_is_valid = bool(
                self.component_count == 0
                and self.leaf_part_count == 1
                and self.unique_part_count == 1
                and self.solid_count == 1
            )
        elif self.result_kind == "assembly":
            structure_is_valid = bool(
                self.component_count is not None
                and self.leaf_part_count is not None
                and self.unique_part_count is not None
                and self.component_count >= self.leaf_part_count >= 1
                and 1 <= self.unique_part_count <= self.leaf_part_count
                and self.solid_count == self.leaf_part_count
            )
        else:
            structure_is_valid = False
        required_entries = {"model.scene.zip", "product.json", "validation.json"}
        return bool(
            self.status == "succeeded"
            and self.exit_code == 0
            and self.final_shape_count == 1
            and structure_is_valid
            and self.solid_volume is not None
            and math.isfinite(self.solid_volume)
            and self.solid_volume > 0
            and self.scene_artifact_exists
            and self.scene_parse_result.valid
            and required_entries.issubset(self.artifact_entries)
            and self.product_manifest_path == "artifacts/product.json"
            and self.product_status == "Draft"
            and self.product_validation_status == "Passed"
            and not self.product_validation_failures
        )

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
            "unique_part_count": self.unique_part_count,
            "product_manifest_path": self.product_manifest_path,
            "product_status": self.product_status,
            "product_validation_status": self.product_validation_status,
            "product_validation_failures": list(self.product_validation_failures),
            "product_validation_checks": list(self.product_validation_checks),
            "execution_phase": self.execution_phase,
            "validation_short_circuited": self.validation_short_circuited,
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
        """Execute ``code/model.py`` in the Project's working directory.

        The source is intentionally executed without policy-based AST, import,
        or dangerous-call inspection. The compile/import preflight only reports
        source errors; it does not reject an API based on its origin. This is
        the trusted-local-demo decision in ADR-0004; the subprocess timeout,
        output bound, environment filtering, and process termination are
        operational controls only.
        """

        started = time.monotonic()
        root = Path(project_dir).expanduser().resolve()
        code_dir = project_code_directory(root)
        model_path = code_dir / MODEL_SOURCE_NAME
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
            # This call also migrates a pre-``code/`` Project once, preserving
            # the source before the trusted subprocess starts.
            scaffold = create_model_source(root, overwrite=False)
            code_dir = scaffold.code_dir
            model_path = scaffold.model_path
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise ValueError("timeout_seconds must be finite and positive")
            if max_output_bytes < 0:
                raise ValueError("max_output_bytes must not be negative")
            if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
                raise ValueError("attempt must be a positive integer")
            self._validate_project_paths(root, code_dir, model_path, artifact_dir)
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
                            str(_RUNNER_PATH),
                            str(model_path),
                            str(root),
                            str(code_dir),
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
        execution_phase = _runner_phase(bytes(stdout_collector.payload))
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
        topology_error = _payload_string(payload, "topology_error")
        reported_unique_part_count = _payload_int(payload, "unique_part_count")
        reported_product_manifest_path = _payload_string(
            payload, "product_manifest_path"
        )
        reported_product_status = _payload_string(payload, "product_status")
        product_validation_status = _payload_string(
            payload, "product_validation_status"
        )
        product_validation_failures = _payload_strings(
            payload, "product_validation_failures"
        )
        validation_short_circuited = _payload_bool(
            payload, "validation_short_circuited"
        )
        product_artifact: ProductArtifact | None = None
        product_artifact_error: str | None = None
        product_manifest_path: str | None = None
        product_status: str | None = None
        product_validation_checks: tuple[dict[str, object], ...] = ()
        unique_part_count: int | None = reported_unique_part_count
        if validation_short_circuited:
            product_status = reported_product_status
            product_validation_checks = _bounded_validation_checks(
                {
                    "checks": (
                        payload.get("product_validation_checks", [])
                        if payload is not None
                        else []
                    )
                }
            )
        else:
            try:
                product_artifact = load_product_artifact(artifact_dir)
                product_artifact.require_complete()
                product_manifest_path = str(
                    (artifact_dir / PRODUCT_ARTIFACT_MANIFEST_NAME).relative_to(root)
                )
                product_status = product_artifact.status.value
                unique_part_count = product_artifact.summary.unique_part_count
                product_validation_checks = _bounded_validation_checks(
                    product_artifact.validation_report
                )
            except (OSError, ProductArtifactError) as exc:
                product_artifact_error = str(exc)
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
                else _timeout_error(timeout_seconds, execution_phase)
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
        elif topology_error is not None:
            status = "failed"
            error = topology_error
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
        elif validation_short_circuited:
            short_circuit_is_valid = bool(
                result_kind == "assembly"
                and product_validation_status == "Draft"
                and product_validation_failures
                and reported_product_status == "Draft"
                and reported_product_manifest_path is None
                and unique_part_count is not None
                and leaf_part_count is not None
                and 1 <= unique_part_count <= leaf_part_count
                and any(
                    check.get("status") == "failed"
                    for check in product_validation_checks
                )
                and not artifact_entries
                and not scene_exists
                and review_artifact_dir is None
                and review_manifest_path is None
                and review_evidence_error is None
            )
            if not short_circuit_is_valid:
                status = "failed"
                error = "CAD process reported an invalid short-circuited Draft"
                error_type = "product_validation"
        elif product_artifact_error is not None or product_artifact is None:
            status = "failed"
            error = (
                "product artifact could not be validated: "
                + (product_artifact_error or "unknown product artifact error")
            )
            error_type = "product_artifact"
        elif product_artifact.result_kind != result_kind:
            status = "failed"
            error = "product artifact result kind does not match Model Source"
            error_type = "product_artifact"
        elif (
            product_artifact.summary.component_count != (component_count or 0)
            or product_artifact.summary.leaf_part_count
            != (leaf_part_count if result_kind == "assembly" else 1)
            or product_artifact.summary.solid_count != solid_count
            or not math.isclose(
                product_artifact.summary.volume_mm3,
                solid_volume,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            status = "failed"
            error = "product artifact summary does not match observed CAD facts"
            error_type = "product_artifact"
        elif (
            reported_product_manifest_path != product_manifest_path
            or reported_product_status != product_status
            or reported_unique_part_count != unique_part_count
        ):
            status = "failed"
            error = "CAD process product artifact report is inconsistent"
            error_type = "product_artifact"
        elif _artifact_has_symlink(artifact_dir):
            status = "failed"
            error = "artifacts must not contain symbolic links"
            error_type = "scene"
        elif artifact_entries != _declared_artifact_entries(product_artifact):
            status = "failed"
            error = "artifacts contain files not declared by product.json"
            error_type = "product_artifact"
        elif product_artifact.file_path("scene") != scene_path:
            status = "failed"
            error = "product artifact Scene must use artifacts/model.scene.zip"
            error_type = "product_artifact"
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
            unique_part_count=unique_part_count,
            product_manifest_path=product_manifest_path,
            product_status=product_status,
            product_validation_status=product_validation_status,
            product_validation_failures=product_validation_failures,
            product_validation_checks=product_validation_checks,
            execution_phase=execution_phase,
            validation_short_circuited=validation_short_circuited,
        )

    @staticmethod
    def _validate_project_paths(
        root: Path, code_dir: Path, model_path: Path, artifact_dir: Path
    ) -> None:
        if not root.is_dir():
            raise ValueError("Project working directory does not exist")
        if code_dir.is_symlink():
            raise ValueError("Project code directory must not be a symlink")
        if not code_dir.is_dir():
            raise ValueError("Project code directory does not exist")
        for source_path in code_dir.rglob("*"):
            if source_path.is_symlink():
                raise ValueError("Project code sources must not be symbolic links")
        if model_path.is_symlink():
            raise ValueError("Model Source must not be a symlink")
        if not _is_under(model_path.resolve(), code_dir):
            raise ValueError("Model Source is outside Project code")
        if not model_path.is_file():
            raise ValueError("current Project Model Source is missing")
        if artifact_dir.is_symlink():
            raise ValueError("Project artifact directory must not be a symlink")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        review_dir = root / ".cad-review"
        if review_dir.is_symlink():
            raise ValueError("Project CAD review directory must not be a symlink")
        if review_dir.exists() and not review_dir.is_dir():
            raise ValueError("Project CAD review path must be a directory")

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


def _runner_phase(raw_stdout: bytes) -> str | None:
    text = raw_stdout.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if not line.startswith(_PHASE_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_PHASE_PREFIX) :])
        except json.JSONDecodeError:
            return None
        phase = payload.get("phase") if isinstance(payload, dict) else None
        return phase if isinstance(phase, str) and phase else None
    return None


def _timeout_error(timeout_seconds: float, phase: str | None) -> str:
    base = f"CAD execution timed out after {timeout_seconds:g} seconds"
    if phase is None:
        return base
    guidance = {
        "model_build": "simplify expensive booleans and build one representative Part first",
        "strict_constraint_solve": "simplify or repair the constraint graph",
        "current_pose_collision": (
            "reduce mesh complexity; exclude only named physical contact pairs"
        ),
        "product_step_export": "simplify product geometry before STEP export",
        "unique_part_step_export": "simplify the slow unique Part geometry",
        "step_export_replay": "simplify exported topology",
        "scene_export": "simplify render geometry",
        "review_evidence": "simplify the final product used for review renders",
    }.get(phase)
    message = f"{base} during {phase}"
    return f"{message}; {guidance}" if guidance else message


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
    values: list[int | None] = []
    for name in ("component_count", "leaf_part_count"):
        value = payload.get(name)
        values.append(
            value if isinstance(value, int) and not isinstance(value, bool) else None
        )
    return values[0], values[1]


def _payload_string(
    payload: dict[str, object] | None,
    name: str,
) -> str | None:
    if payload is None:
        return None
    value = payload.get(name)
    return value if isinstance(value, str) and value else None


def _payload_int(
    payload: dict[str, object] | None,
    name: str,
) -> int | None:
    if payload is None:
        return None
    value = payload.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _payload_bool(
    payload: dict[str, object] | None,
    name: str,
) -> bool:
    if payload is None:
        return False
    value = payload.get(name)
    return value if isinstance(value, bool) else False


def _payload_strings(
    payload: dict[str, object] | None,
    name: str,
) -> tuple[str, ...]:
    if payload is None:
        return ()
    value = payload.get(name)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return ()
    return tuple(value)


def _bounded_validation_checks(
    validation_report: Mapping[str, object] | None,
) -> tuple[dict[str, object], ...]:
    """Project verified validation evidence into a bounded Agent-facing result."""

    if not isinstance(validation_report, Mapping):
        return ()
    raw_checks = validation_report.get("checks")
    if not isinstance(raw_checks, list):
        return ()
    ranked_checks = sorted(
        enumerate(raw_checks),
        key=lambda item: (
            not (
                isinstance(item[1], Mapping)
                and item[1].get("status") == "failed"
            ),
            item[0],
        ),
    )
    checks: list[dict[str, object]] = []
    for _, raw_check in ranked_checks[:_VALIDATION_CHECK_LIMIT]:
        if not isinstance(raw_check, Mapping):
            continue
        check_id = raw_check.get("check_id")
        status = raw_check.get("status")
        if not isinstance(check_id, str) or status not in {
            "passed",
            "failed",
            "not_applicable",
        }:
            continue
        check: dict[str, object] = {"check_id": check_id, "status": status}
        message = raw_check.get("message")
        if isinstance(message, str):
            check["message"] = _bounded_validation_text(message)
        evidence = raw_check.get("evidence")
        if isinstance(evidence, Mapping):
            budget = [_VALIDATION_NODE_LIMIT]
            bounded_evidence, truncated = _bounded_validation_value(
                evidence,
                key="evidence",
                depth=0,
                budget=budget,
            )
            if isinstance(bounded_evidence, dict):
                check["evidence"] = bounded_evidence
            if truncated or raw_check.get("evidence_truncated") is True:
                check["evidence_truncated"] = True
        checks.append(check)
    return tuple(checks)


def _bounded_validation_value(
    value: object,
    *,
    key: str,
    depth: int,
    budget: list[int],
) -> tuple[object | None, bool]:
    if budget[0] <= 0 or depth > _VALIDATION_DEPTH_LIMIT:
        return None, True
    budget[0] -= 1
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if isinstance(value, str):
        bounded = _bounded_validation_text(value)
        return bounded, bounded != value
    if isinstance(value, Mapping):
        bounded_mapping: dict[str, object] = {}
        truncated = len(value) > _VALIDATION_MAPPING_LIMIT
        for raw_key, child in list(value.items())[:_VALIDATION_MAPPING_LIMIT]:
            if not isinstance(raw_key, str):
                truncated = True
                continue
            bounded_child, child_truncated = _bounded_validation_value(
                child,
                key=raw_key,
                depth=depth + 1,
                budget=budget,
            )
            if budget[0] <= 0 and bounded_child is None:
                truncated = True
                break
            bounded_mapping[raw_key] = bounded_child
            truncated = truncated or child_truncated
        return bounded_mapping, truncated
    if isinstance(value, (list, tuple)):
        limit = _VALIDATION_SEQUENCE_LIMITS.get(key, _VALIDATION_SEQUENCE_LIMIT)
        bounded_items: list[object] = []
        truncated = len(value) > limit
        for child in value[:limit]:
            bounded_child, child_truncated = _bounded_validation_value(
                child,
                key=key,
                depth=depth + 1,
                budget=budget,
            )
            if budget[0] <= 0 and bounded_child is None:
                truncated = True
                break
            bounded_items.append(bounded_child)
            truncated = truncated or child_truncated
        return bounded_items, truncated
    return _bounded_validation_text(str(value)), True


def _bounded_validation_text(value: str) -> str:
    if len(value) <= _VALIDATION_TEXT_LIMIT:
        return value
    return value[: _VALIDATION_TEXT_LIMIT - 3] + "..."


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
        if path.is_file() or path.is_symlink():
            entries.append(relative.as_posix())
    return tuple(sorted(entries))


def _declared_artifact_entries(artifact: ProductArtifact) -> tuple[str, ...]:
    entries = {PRODUCT_ARTIFACT_MANIFEST_NAME}
    entries.update(record.relative_path for record in artifact.files.values())
    entries.update(part.file.relative_path for part in artifact.parts)
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




__all__ = [
    "CAD_EXECUTION_TIMEOUT_SECONDS",
    "CADExecutor",
    "CancellationToken",
    "DEFAULT_OUTPUT_BYTES",
    "ExecutionResult",
    "build_cad_environment",
    "redact_credentials",
]
