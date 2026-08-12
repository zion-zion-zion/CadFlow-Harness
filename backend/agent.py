"""Reference-grounded Deep Agent orchestration for one CAD generation run."""

from __future__ import annotations

import math
import os
import inspect
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from langchain_core.tools import BaseTool, tool

from .cad_executor import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    CancellationToken,
    ExecutionResult,
    redact_credentials,
)
from .contracts import ToolUseRecord
from .events import ProgressUpdate
from .projects import ProjectStore
from .projects import ProjectState
from .restricted_tools import AgentModelValidator
from .scene_validation import SceneParseResult


class AgentConfigurationError(RuntimeError):
    """Raised when the backend model configuration is incomplete."""


class AgentRunError(RuntimeError):
    """Raised when a run cannot produce a structured Agent outcome."""


class AgentRunCancelled(AgentRunError):
    """Raised inside a tool when the user has stopped the current Agent Run."""


MAX_CAD_EXECUTIONS = 3
MAX_AGENT_RUN_SECONDS = 5 * 60.0
AGENT_RUN_TIMEOUT_SECONDS = MAX_AGENT_RUN_SECONDS
MAX_PROVIDER_RETRIES = 2


@dataclass(frozen=True)
class AgentSettings:
    """Backend-only configuration for the single OpenAI-compatible model."""

    model_id: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    provider: str = "openai"

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "AgentSettings":
        values = os.environ if environment is None else environment
        missing = [
            name
            for name in ("OPENAI_API_KEY", "OPENAI_MODEL_ID")
            if not values.get(name, "").strip()
        ]
        if missing:
            raise AgentConfigurationError(
                "missing required Agent configuration: " + ", ".join(missing)
            )
        base_url = values.get("OPENAI_BASE_URL", "").strip() or None
        return cls(
            model_id=values["OPENAI_MODEL_ID"].strip(),
            api_key=values["OPENAI_API_KEY"],
            base_url=base_url,
        )

    @property
    def deep_agent_model_spec(self) -> str:
        return f"{self.provider}:{self.model_id}"


@dataclass(frozen=True)
class AgentRunOutcome:
    """Safe outcome of one bounded Text-to-CAD Agent Run."""

    validated: bool
    failure_reason: str | None = None
    execution_result: ExecutionResult | None = None
    tool_use_records: tuple[ToolUseRecord, ...] = ()
    execution_results: tuple[ExecutionResult, ...] = ()
    provider_retry_count: int = 0
    duration_seconds: float | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        results = self.execution_results
        if not results and self.execution_result is not None:
            object.__setattr__(self, "execution_results", (self.execution_result,))
        elif results and self.execution_result is None:
            object.__setattr__(self, "execution_result", results[-1])

    @property
    def status(self) -> str:
        if self.cancelled:
            return "cancelled"
        return "succeeded" if self.validated else "failed"

    def diagnostics(self) -> dict[str, Any]:
        results = self.execution_results
        return {
            "status": self.status,
            "failure_reason": self.failure_reason,
            "execution_result": (
                self.execution_result.to_dict()
                if self.execution_result is not None
                else None
            ),
            "cad_execution_count": len(results),
            "execution_results": [
                {
                    "attempt": index,
                    **result.to_dict(),
                }
                for index, result in enumerate(results, start=1)
            ],
            "provider_retry_count": self.provider_retry_count,
            "duration_seconds": self.duration_seconds,
            "cancelled": self.cancelled,
            "tool_use_records": [
                {
                    "sequence": record.sequence,
                    "tool_name": record.tool_name,
                    "target": record.target,
                    "reference_names": list(record.reference_names),
                }
                for record in self.tool_use_records
            ],
        }


_SYSTEM_PROMPT = """You are the one and only primary Text-to-CAD Agent for this run.

Treat the user's Prompt as complete. Never ask a clarification question or wait
for another turn. If a length unit is omitted, use millimetres. If dimensions or
construction details are underspecified, choose reasonable engineering values
and record the important assumptions in comments near the top of Model Source.

Use a run-local plan with write_todos when useful. You have general filesystem,
search, editing, and local shell tools. Use them freely to inspect the workspace,
read the loaded SimpleCADAPI Skill, run Python, create diagnostic scripts,
inspect generated artifacts, and install or use dependencies when they help.
The shell is a trusted local development environment, not a sandbox. Keep the
final CAD implementation in the current Project's model.py and use
SimpleCADAPI as its modeling API.

Begin from the current Model Source, but you may replace the complete file and
add imports, local modules, helper functions, or useful dependencies. Use the
built-in file tools to edit it. Keep one top-level model entry point, one
physical part, and one captured final Solid. Keep the canonical
artifacts/model.scene.zip export contract.

After writing the complete Model Source, call validate_model. Its structured
result is the source of truth: inspect status, exit_code, error, stdout,
stderr, captured_solid_count, solid_volume, scene_artifact_exists,
scene_parse_result, and artifact_entries. If the result is not fully valid,
diagnose the reported facts, replace the complete current Model Source with a
repair, and execute it again. A repair must use the latest source and may add
imports or helpers, but must preserve the one-part and canonical Scene
Artifact contract. Continue only while the bounded tool allows another CAD
execution; never evade that limit by replanning, using another tool, or
creating another Agent. Stop as soon as a Validated Result is reported.
"""


def build_chat_model(settings: AgentSettings) -> Any:
    """Construct the configured LangChain model without exposing its key."""

    from langchain_openai import ChatOpenAI

    arguments: dict[str, Any] = {
        "model": settings.model_id,
        "api_key": settings.api_key,
        "max_retries": MAX_PROVIDER_RETRIES,
        "timeout": MAX_AGENT_RUN_SECONDS,
    }
    if settings.base_url is not None:
        arguments["base_url"] = settings.base_url
    return ChatOpenAI(**arguments)


def create_agent_tools(
    validator: AgentModelValidator,
    *,
    max_executions: int = 1,
    on_execution: Callable[[ExecutionResult], None] | None = None,
    on_execution_error: Callable[[ExecutionResult], None] | None = None,
    run_deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    cancellation_token: object | None = None,
    on_progress: Callable[[ProgressUpdate], None] | None = None,
) -> tuple[BaseTool, ...]:
    """Expose one zero-argument structured CAD validation tool."""

    if max_executions < 1:
        raise ValueError("max_executions must be positive")
    execution_attempts = 0
    def require_time_remaining() -> float | None:
        if _is_cancellation_requested(cancellation_token):
            raise AgentRunCancelled("Agent Run stopped by caller")
        if run_deadline is None:
            return None
        remaining = run_deadline - clock()
        if remaining <= 0:
            raise AgentRunError("Agent Run exceeded the five-minute wall-clock limit")
        return remaining

    @tool("validate_model")
    def validate_model() -> dict[str, Any]:
        """Run and structurally validate the current model.py and Scene Artifact."""

        nonlocal execution_attempts
        if execution_attempts >= max_executions:
            execution_word = "execution" if max_executions == 1 else "executions"
            raise AgentRunError(
                f"this Agent Run allows at most {max_executions} CAD {execution_word}"
            )
        remaining = require_time_remaining()
        execution_attempts += 1
        execution_recorded = False
        try:
            result = validator.validate_model(
                cancellation_token=cancellation_token,
                timeout_seconds=(
                    min(CAD_EXECUTION_TIMEOUT_SECONDS, remaining)
                    if remaining is not None
                    else None
                ),
            )
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
        if on_execution is not None and not execution_recorded:
            on_execution(result)
        if on_progress is not None:
            on_progress(
                ProgressUpdate(
                    stage="executing",
                    tool="cad",
                    attempt=execution_attempts,
                    result=_execution_progress_result(result),
                )
            )
        return result.to_dict()

    return (validate_model,)


def build_deep_agent(
    settings: AgentSettings,
    tools: Sequence[BaseTool],
    *,
    model: Any | None = None,
    workspace_root: str | Path | None = None,
    skill_root: str | Path | None = None,
) -> Any:
    """Build one Deep Agent with general local tools and CAD-specific tools."""

    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from deepagents.backends import LocalShellBackend
    from langchain.agents.middleware import TodoListMiddleware

    def planning_middleware() -> list[Any]:
        return [TodoListMiddleware()]

    register_harness_profile(
        settings.deep_agent_model_spec,
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            extra_middleware=planning_middleware,
        ),
    )
    resolved_model = build_chat_model(settings) if model is None else model
    backend = LocalShellBackend(
        root_dir=workspace_root or Path.cwd(),
        virtual_mode=False,
        timeout=int(CAD_EXECUTION_TIMEOUT_SECONDS),
        inherit_env=True,
    )
    return create_deep_agent(
        model=resolved_model,
        tools=tuple(tools),
        system_prompt=_SYSTEM_PROMPT,
        subagents=(),
        skills=[str(skill_root)] if skill_root is not None else None,
        backend=backend,
        memory=None,
        checkpointer=None,
        store=None,
        name="text-to-cad-primary",
    )


class ReferenceGroundedAgent:
    """Run one primary Deep Agent with a bounded repair budget."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        repo_root: str | Path,
        project_dir: str | Path,
        executor: Any | None = None,
        model: Any | None = None,
    ) -> None:
        self.settings = settings
        self.repo_root = repo_root
        self.project_dir = project_dir
        self.executor = executor
        self.model = model

    def run(
        self,
        prompt: str,
        *,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
    ) -> AgentRunOutcome:
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentRunError("Prompt must not be empty")
        started = time.monotonic()
        deadline = started + MAX_AGENT_RUN_SECONDS
        token = cancellation_token or CancellationToken()

        def emit(update: ProgressUpdate) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(update)
            except Exception:
                # A local event sink must not turn a valid CAD outcome into a
                # model failure when observability has a transient error.
                return

        def on_tool_use(record: ToolUseRecord) -> None:
            if record.tool_name == "prepare_model_source":
                emit(ProgressUpdate(stage="preparing", tool="project"))

        validator = AgentModelValidator(
            repo_root=self.repo_root,
            project_dir=self.project_dir,
            executor=self.executor,
            on_tool_use=on_tool_use,
        )
        validator.begin_run()
        if token.cancelled and token.cancellation_reason != "timeout":
            return _cancelled_outcome(
                validator,
                execution_results=(),
                duration=time.monotonic() - started,
            )
        executions: list[ExecutionResult] = []

        def record_execution(result: ExecutionResult) -> None:
            executions.append(result)

        agent_tools = create_agent_tools(
            validator,
            max_executions=MAX_CAD_EXECUTIONS,
            on_execution=record_execution,
            on_execution_error=record_execution,
            run_deadline=deadline,
            cancellation_token=token,
            on_progress=emit,
        )
        agent_error: str | None = None
        timed_out = False
        try:
            agent = build_deep_agent(
                self.settings,
                agent_tools,
                model=self.model,
                workspace_root=self.project_dir,
                skill_root=Path(self.repo_root) / "skills",
            )
            agent_error, timed_out = _invoke_agent_with_deadline(
                agent,
                prompt,
                deadline=deadline,
                cancellation_token=token,
            )
        except Exception as exc:  # Agent/provider errors become a safe diagnosis.
            agent_error = _safe_failure_reason(exc)

        duration = time.monotonic() - started
        execution = executions[-1] if executions else None
        if timed_out or token.cancellation_reason == "timeout" or duration > MAX_AGENT_RUN_SECONDS:
            return AgentRunOutcome(
                validated=False,
                failure_reason="Agent Run exceeded the five-minute wall-clock limit",
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=validator.tool_use_records,
                duration_seconds=duration,
            )
        if token.cancelled:
            return _cancelled_outcome(
                validator,
                execution_results=tuple(executions),
                duration=duration,
            )
        if execution is None:
            return AgentRunOutcome(
                validated=False,
                failure_reason=agent_error
                or "Agent finished without executing the Model Source",
                tool_use_records=validator.tool_use_records,
                duration_seconds=duration,
            )
        if not _is_validated_result(execution):
            return AgentRunOutcome(
                validated=False,
                failure_reason=_safe_failure_text(execution.error)
                or agent_error
                or "CAD execution did not produce a Validated Result",
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=validator.tool_use_records,
                duration_seconds=duration,
            )
        return AgentRunOutcome(
            validated=True,
            execution_result=execution,
            execution_results=tuple(executions),
            tool_use_records=validator.tool_use_records,
            duration_seconds=duration,
        )


class AgentRunService:
    """Submit one Prompt and persist the first Deep Agent outcome."""

    def __init__(
        self,
        *,
        store: ProjectStore,
        repo_root: str | Path,
        settings_factory: Callable[[], AgentSettings] | None = None,
        agent_factory: Callable[..., ReferenceGroundedAgent] = ReferenceGroundedAgent,
    ) -> None:
        self.store = store
        self.repo_root = repo_root
        self.settings_factory = settings_factory or AgentSettings.from_environment
        self.agent_factory = agent_factory

    def run(
        self,
        project_id: str,
        prompt: str,
        *,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
        prompt_submitted: bool = False,
    ) -> AgentRunOutcome:
        project = (
            self.store.get_project(project_id)
            if prompt_submitted
            else self.store.submit_prompt(project_id, prompt)
        )
        if prompt_submitted and project.state != ProjectState.RUNNING:
            raise AgentRunError("Agent Run requires a Running Project")
        token = cancellation_token or CancellationToken()
        if token.cancelled:
            outcome = AgentRunOutcome(
                validated=False,
                cancelled=True,
                failure_reason="Agent Run stopped by caller",
            )
            self.store.mark_stopped(
                project.project_id,
                outcome.failure_reason or "Agent Run stopped by caller",
                outcome.diagnostics(),
            )
            return outcome
        try:
            settings = self.settings_factory()
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
            if token.cancelled:
                outcome = replace(
                    outcome,
                    cancelled=True,
                    failure_reason="Agent Run stopped by caller",
                )
                self.store.mark_stopped(
                    project.project_id,
                    outcome.failure_reason or "Agent Run stopped by caller",
                    outcome.diagnostics(),
                )
            else:
                self.store.mark_failed(
                    project.project_id, reason, outcome.diagnostics()
                )
            return outcome

        try:
            agent = self.agent_factory(
                settings=settings,
                repo_root=self.repo_root,
                project_dir=self.store.project_directory(project.project_id),
            )
            outcome = _invoke_agent_run(
                agent,
                project.prompt or prompt,
                cancellation_token=token,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
        if (
            token.cancelled
            and token.cancellation_reason != "timeout"
            and not outcome.cancelled
        ):
            outcome = replace(
                outcome,
                validated=False,
                cancelled=True,
                failure_reason="Agent Run stopped by caller",
            )
        if outcome.cancelled:
            self.store.mark_stopped(
                project.project_id,
                outcome.failure_reason or "Agent Run stopped by caller",
                outcome.diagnostics(),
            )
            return outcome
        if outcome.validated:
            try:
                self.store.mark_succeeded(project.project_id, outcome.diagnostics())
            except Exception as exc:
                reason = _safe_failure_reason(exc)
                outcome = replace(
                    outcome,
                    validated=False,
                    failure_reason=reason,
                )
                self.store.mark_failed(
                    project.project_id,
                    reason,
                    outcome.diagnostics(),
                )
        else:
            self.store.mark_failed(
                project.project_id,
                outcome.failure_reason or "Agent Run failed",
                outcome.diagnostics(),
            )
        return outcome


def _invoke_agent_run(
    agent: Any,
    prompt: str,
    *,
    cancellation_token: CancellationToken,
    progress_callback: Callable[[ProgressUpdate], None] | None,
) -> AgentRunOutcome:
    """Call old issue-03 test adapters and the cancellable Agent uniformly."""

    run: Any = agent.run
    kwargs: dict[str, object] = {}
    parameters: Mapping[str, inspect.Parameter]
    try:
        parameters = inspect.signature(run).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs or "cancellation_token" in parameters:
        kwargs["cancellation_token"] = cancellation_token
    if accepts_kwargs or "progress_callback" in parameters:
        kwargs["progress_callback"] = progress_callback
    return run(prompt, **kwargs)


def _is_validated_result(result: ExecutionResult) -> bool:
    return bool(
        result.status == "succeeded"
        and result.exit_code == 0
        and result.captured_solid_count == 1
        and result.solid_volume is not None
        and math.isfinite(result.solid_volume)
        and result.solid_volume > 0
        and result.scene_artifact_exists
        and result.scene_parse_result.valid
        and result.artifact_entries == ("model.scene.zip",)
    )


def _is_cancellation_requested(token: object | None) -> bool:
    if token is None:
        return False
    cancelled = getattr(token, "cancelled", False)
    return bool(cancelled() if callable(cancelled) else cancelled)


def _cancelled_outcome(
    validator: AgentModelValidator,
    *,
    execution_results: tuple[ExecutionResult, ...],
    duration: float,
) -> AgentRunOutcome:
    execution = execution_results[-1] if execution_results else None
    return AgentRunOutcome(
        validated=False,
        cancelled=True,
        failure_reason="Agent Run stopped by caller",
        execution_result=execution,
        execution_results=execution_results,
        tool_use_records=validator.tool_use_records,
        duration_seconds=duration,
    )


def _execution_progress_result(result: ExecutionResult) -> str:
    if result.status == "succeeded":
        return "CAD execution completed; validating Scene Artifact"
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
        captured_solid_count=None,
        solid_volume=None,
        scene_artifact_exists=False,
        scene_parse_result=SceneParseResult(
            valid=False,
            error="CAD execution failed before producing a Scene Artifact",
        ),
        artifact_entries=(),
        duration_seconds=0.0,
    )


def _invoke_agent_with_deadline(
    agent: Any,
    prompt: str,
    *,
    deadline: float,
    cancellation_token: CancellationToken,
) -> tuple[str | None, bool]:
    """Invoke the primary Agent without allowing a blocking call past the budget."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        cancellation_token.cancel(reason="timeout")
        return "Agent Run exceeded the five-minute wall-clock limit", True

    error: list[Exception] = []

    def invoke() -> None:
        try:
            agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Complete this CAD generation request in the current "
                                f"Project: {prompt}"
                            ),
                        }
                    ]
                }
            )
        except Exception as exc:
            error.append(exc)

    worker = threading.Thread(target=invoke, name="text-to-cad-agent", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(min(0.05, remaining))
        if cancellation_token.cancelled:
            return "Agent Run stopped by caller", False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            cancellation_token.cancel(reason="timeout")
            return "Agent Run exceeded the five-minute wall-clock limit", True
    if error:
        return _safe_failure_reason(error[0]), False
    return None, False


def _safe_failure_text(message: str | None) -> str | None:
    if not message:
        return None
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return redact_credentials(first_line)[:500] or None


def _safe_failure_reason(error: Exception) -> str:
    return _safe_failure_text(str(error)) or type(error).__name__


__all__ = [
    "AgentConfigurationError",
    "AgentRunError",
    "AgentRunCancelled",
    "AgentRunOutcome",
    "AgentRunService",
    "AgentSettings",
    "AGENT_RUN_TIMEOUT_SECONDS",
    "MAX_AGENT_RUN_SECONDS",
    "MAX_CAD_EXECUTIONS",
    "MAX_PROVIDER_RETRIES",
    "ReferenceGroundedAgent",
    "build_chat_model",
    "build_deep_agent",
    "create_agent_tools",
]
