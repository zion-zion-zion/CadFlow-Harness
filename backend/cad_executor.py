"""Stable CAD execution boundary and compatibility exports.

The public module deliberately remains small. Process lifecycle, protocol
decoding, security filtering, result contracts, and host artifact checks live
in focused internal modules; this class only coordinates their existing
behavior.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from .cad_artifacts import (
    clear_artifacts,
    collect_artifact_facts,
    validate_host_artifacts,
    validate_project_paths,
)
from .cad_execution_contract import ExecutionResult
from .cad_process import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    CancellationToken,
    DEFAULT_OUTPUT_BYTES,
    is_cancelled,
    execute_cad_process,
)
from .cad_protocol import (
    assembly_facts,
    classify_process_error,
    first_error,
    looks_like_import_error,
    module_names,
    payload_bool,
    payload_int,
    payload_string,
    payload_strings,
    preflight_source,
    result_kind as parse_result_kind,
    review_facts,
    runner_payload,
    runner_phase,
    runner_preflight_payload,
    shape_facts,
    timeout_error,
    traceback_location,
)
from .cad_security import build_cad_environment, redact_credentials, safe_output
from .model_source import (
    ARTIFACT_DIRECTORY_NAME,
    MODEL_SOURCE_NAME,
    SCENE_ARTIFACT_NAME,
    create_model_source,
    project_code_directory,
)


_RUNNER_PATH = Path(__file__).with_name("cad_runner.py").resolve()


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
        """Execute ``code/model.py`` and independently verify its artifacts."""

        started = time.monotonic()
        root = Path(project_dir).expanduser().resolve()
        code_dir = project_code_directory(root)
        model_path = code_dir / MODEL_SOURCE_NAME
        artifact_dir = root / ARTIFACT_DIRECTORY_NAME
        scene_path = artifact_dir / SCENE_ARTIFACT_NAME

        process_id: int | None = None
        exit_code: int | None = None
        forced_status: str | None = None
        launch_error: str | None = None
        preflight_error: str | None = None
        preflight_location: str | None = None
        preflight_status = "not_run"
        imported_modules: tuple[str, ...] = ()
        stdout_raw = b""
        stderr_raw = b""
        stdout_truncated = False
        stderr_truncated = False

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
            validate_project_paths(root, code_dir, model_path, artifact_dir)
            clear_artifacts(artifact_dir)
            if is_cancelled(cancellation_token):
                forced_status = "cancelled"
            else:
                preflight_error, preflight_location = preflight_source(model_path)
                if preflight_error is None:
                    preflight_status = "passed"
                    process_result = execute_cad_process(
                        model_path=model_path,
                        project_root=root,
                        code_root=code_dir,
                        runner_path=_RUNNER_PATH,
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=max_output_bytes,
                        cancellation_token=cancellation_token,
                        started=started,
                    )
                    process_id = process_result.process_id
                    exit_code = process_result.exit_code
                    forced_status = process_result.forced_status
                    launch_error = process_result.launch_error
                    stdout_raw = process_result.stdout
                    stderr_raw = process_result.stderr
                    stdout_truncated = process_result.stdout_truncated
                    stderr_truncated = process_result.stderr_truncated
                else:
                    preflight_status = "failed"
        except (OSError, ValueError) as exc:
            launch_error = str(exc)
        duration = time.monotonic() - started

        stdout, stdout_was_truncated = safe_output(
            stdout_raw,
            max_output_bytes,
            already_truncated=stdout_truncated,
        )
        stderr, stderr_was_truncated = safe_output(
            stderr_raw,
            max_output_bytes,
            already_truncated=stderr_truncated,
        )
        payload = runner_payload(stdout_raw)
        execution_phase = runner_phase(stdout_raw)
        preflight_payload = runner_preflight_payload(stdout_raw)
        if preflight_payload is not None:
            imported_modules = module_names(preflight_payload)
        elif (
            preflight_status == "passed"
            and exit_code != 0
            and looks_like_import_error(stderr, stdout)
        ):
            preflight_status = "failed"

        shape_count, solid_count, solid_volume = shape_facts(payload)
        result_kind = parse_result_kind(payload)
        component_count, leaf_part_count = assembly_facts(payload)
        topology_error = payload_string(payload, "topology_error")
        reported_unique_part_count = payload_int(payload, "unique_part_count")
        reported_product_manifest_path = payload_string(
            payload, "product_manifest_path"
        )
        reported_product_status = payload_string(payload, "product_status")
        product_validation_status = payload_string(
            payload, "product_validation_status"
        )
        product_validation_failures = payload_strings(
            payload, "product_validation_failures"
        )
        validation_short_circuited = payload_bool(
            payload, "validation_short_circuited"
        )
        (
            review_artifact_dir,
            review_manifest_path,
            review_model_sha256,
            review_evidence_error,
        ) = review_facts(payload)

        artifact_facts = collect_artifact_facts(
            root=root,
            artifact_dir=artifact_dir,
            scene_path=scene_path,
            payload=payload,
            validation_short_circuited=validation_short_circuited,
        )

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
                else timeout_error(timeout_seconds, execution_phase)
            )
            error_type = forced_status
        elif exit_code != 0:
            status = "failed"
            error = first_error(
                stderr, stdout, "CAD process exited with a non-zero status"
            )
            error_type = classify_process_error(stderr, stdout)
            error_location = traceback_location(stderr, stdout)
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
            error = (
                f"Model Source returned {shape_count or 0} results; "
                "expected exactly one"
            )
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
                else (
                    "final Shape must be solid-compatible and contain exactly one solid"
                )
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
        else:
            artifact_validation = validate_host_artifacts(
                root=root,
                artifact_dir=artifact_dir,
                scene_path=scene_path,
                validation_short_circuited=validation_short_circuited,
                result_kind=result_kind,
                component_count=component_count,
                leaf_part_count=leaf_part_count,
                solid_count=solid_count,
                solid_volume=solid_volume,
                reported_product_manifest_path=reported_product_manifest_path,
                reported_product_status=reported_product_status,
                reported_unique_part_count=reported_unique_part_count,
                product_validation_status=product_validation_status,
                product_validation_failures=product_validation_failures,
                review_artifact_dir=review_artifact_dir,
                review_manifest_path=review_manifest_path,
                review_evidence_error=review_evidence_error,
                facts=artifact_facts,
            )
            if artifact_validation.error is not None:
                status = "failed"
                error = artifact_validation.error
                error_type = artifact_validation.error_type

        if error_location is None and error_type in {"syntax", "import", "api"}:
            error_location = traceback_location(stderr, stdout)

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
            scene_artifact_exists=artifact_facts.scene_exists,
            scene_parse_result=artifact_facts.scene_parse,
            artifact_entries=artifact_facts.artifact_entries,
            duration_seconds=duration,
            process_id=process_id,
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
            unique_part_count=artifact_facts.unique_part_count,
            product_manifest_path=artifact_facts.product_manifest_path,
            product_status=artifact_facts.product_status,
            product_validation_status=product_validation_status,
            product_validation_failures=product_validation_failures,
            product_validation_checks=artifact_facts.product_validation_checks,
            execution_phase=execution_phase,
            validation_short_circuited=validation_short_circuited,
        )


__all__ = [
    "CAD_EXECUTION_TIMEOUT_SECONDS",
    "CADExecutor",
    "CancellationToken",
    "DEFAULT_OUTPUT_BYTES",
    "ExecutionResult",
    "build_cad_environment",
    "redact_credentials",
]
