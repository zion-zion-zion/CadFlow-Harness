"""CAD-specific tools exposed to the primary Agent."""

from __future__ import annotations

import time
from typing import Any, Callable, Sequence

from langchain_core.tools import BaseTool, tool

from ..cad_execution_contract import ExecutionResult
from ..cad_process import CAD_EXECUTION_TIMEOUT_SECONDS
from ..cad_security import redact_credentials
from ..cad_review import ReviewResult, review_cad
from ..events import ProgressUpdate
from ..restricted_tools import AgentModelValidator
from ..scene_validation import SceneParseResult
from .outcome import AgentRunCancelled, AgentRunError
from .settings import DEFAULT_AGENT_RUN_TIMEOUT_SECONDS, _agent_run_timeout_message


def create_agent_tools(
    validator: AgentModelValidator,
    *,
    request_text: str | None = None,
    review_settings: Any | None = None,
    reviewer_factory: Callable[[Any], Any] | None = None,
    reviewer_callbacks: Sequence[Any] | None = None,
    on_execution: Callable[[ExecutionResult], None] | None = None,
    on_execution_error: Callable[[ExecutionResult], None] | None = None,
    on_review: Callable[[ReviewResult], None] | None = None,
    run_deadline: float | None = None,
    run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    cancellation_token: object | None = None,
    on_progress: Callable[[ProgressUpdate], None] | None = None,
) -> tuple[BaseTool, ...]:
    """Expose zero-argument CAD validation and review tools."""

    execution_attempts = 0
    latest_execution: ExecutionResult | None = None
    last_executed_source_revision: str | None = None
    timeout_message = _agent_run_timeout_message(run_timeout_seconds)

    def require_time_remaining() -> float | None:
        if _is_cancellation_requested(cancellation_token):
            raise AgentRunCancelled("Agent Run stopped by caller")
        if run_deadline is None:
            return None
        remaining = run_deadline - clock()
        if remaining <= 0:
            raise AgentRunError(timeout_message)
        return remaining

    @tool("validate_model")
    def validate_model() -> dict[str, Any]:
        """Validate the product and return bounded, structured failure evidence."""

        nonlocal execution_attempts, latest_execution, last_executed_source_revision
        remaining = require_time_remaining()
        source_revision = validator.source_revision()
        if source_revision == last_executed_source_revision:
            raise AgentRunError(
                "CAD source has not changed since the previous validate_model call. "
                "Repair the Python source before validating again."
            )
        execution_attempts += 1
        execution_recorded = False
        structured_result_received = False

        try:
            result = validator.validate_model(
                cancellation_token=cancellation_token,
                timeout_seconds=(
                    min(CAD_EXECUTION_TIMEOUT_SECONDS, remaining)
                    if remaining is not None
                    else None
                ),
                attempt=execution_attempts,
            )
            structured_result_received = isinstance(result, ExecutionResult)
        except Exception as exc:
            if on_execution_error is None:
                raise
            result = _execution_failure_result(exc)
            on_execution_error(result)
            execution_recorded = True
        if not isinstance(result, ExecutionResult):
            error = AgentRunError("validate_model returned an invalid structured result")
            if on_execution_error is None:
                raise error
            result = _execution_failure_result(error)
            on_execution_error(result)
            execution_recorded = True
        if structured_result_received:
            last_executed_source_revision = source_revision
        if on_execution is not None and not execution_recorded:
            on_execution(result)
        latest_execution = result
        if on_progress is not None:
            on_progress(
                ProgressUpdate(
                    stage="executing",
                    tool="cad",
                    attempt=execution_attempts,
                    result=_execution_progress_result(result),
                )
            )
        payload = result.to_dict()
        if run_deadline is not None:
            payload["run_time_remaining_seconds"] = max(
                0.0,
                round(run_deadline - clock(), 3),
            )
        if result.product_validation_status == "Passed":
            payload["next_action"] = "cad_review_if_complete"
        return payload

    @tool("cad_review")
    def cad_review() -> dict[str, Any]:
        """Review the latest validated CAD model against the user's request.

        This tool is mandatory after the complete requested geometry passes
        its final validate_model call. Do not call it for successful
        intermediate checkpoints in a staged build. It returns a bounded
        pass/fail result with structured findings; it never edits `/code` or
        runs a repair loop.
        """

        remaining = require_time_remaining()
        del remaining  # The reviewer client owns its own bounded timeout.
        if latest_execution is None:
            result = ReviewResult(
                status="fail",
                summary="cad_review requires a prior validate_model call.",
                findings=(),
            )
        else:
            validator.record_tool_use("cad_review", "code/model.py and .cad-review")
            try:
                source = validator.model_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                result = ReviewResult(
                    status="fail",
                    summary="CAD review could not read code/model.py.",
                    findings=(),
                )
            else:
                result = review_cad(
                    project_dir=validator.project_dir,
                    request_text=request_text or "",
                    model_source=source,
                    execution_result=latest_execution,
                    settings=review_settings,
                    reviewer_factory=reviewer_factory,
                    reviewer_callbacks=reviewer_callbacks,
                )
        if on_review is not None:
            on_review(result)
        return result.to_dict()

    return (validate_model, cad_review)


def _is_cancellation_requested(token: object | None) -> bool:
    if token is None:
        return False
    cancelled = getattr(token, "cancelled", False)
    return bool(cancelled() if callable(cancelled) else cancelled)


def _execution_progress_result(result: ExecutionResult) -> str:
    if result.status == "succeeded":
        if result.product_validation_status == "Passed":
            return "CAD product passed deterministic validation; review is ready"
        failures = "; ".join(result.product_validation_failures[:3])
        return "CAD product remains Draft" + (f": {failures}" if failures else "")
    if result.status == "cancelled":
        return "CAD execution cancelled"
    return "CAD execution failed: " + (
        _safe_failure_text(result.error) or "structured diagnosis retained"
    )


def _execution_failure_result(error: Exception) -> ExecutionResult:
    """Turn a CAD boundary exception into the same safe shape as subprocess output."""

    return ExecutionResult(
        status="failed",
        exit_code=None,
        error=_safe_failure_reason(error),
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        final_shape_count=None,
        solid_count=None,
        solid_volume=None,
        scene_artifact_exists=False,
        scene_parse_result=SceneParseResult(
            valid=False,
            error="CAD execution failed before producing a Scene Artifact",
        ),
        artifact_entries=(),
        duration_seconds=0.0,
    )


def _safe_failure_text(message: str | None) -> str | None:
    if not message:
        return None
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return redact_credentials(first_line)[:500] or None


def _safe_failure_reason(error: Exception) -> str:
    return _safe_failure_text(str(error)) or type(error).__name__


__all__ = ["create_agent_tools"]
