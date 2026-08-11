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
from .references import ReferenceContractError
from .restricted_tools import RestrictedAgentTools
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
    """Safe outcome of one bounded reference-grounded Agent Run."""

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


RESTRICTED_BUILTIN_TOOL_NAMES = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "delete_file",
        "delete",
        "task",
    }
)

_SYSTEM_PROMPT = """You are the one and only primary Text-to-CAD Agent for this run.

Treat the user's Prompt as complete. Never ask a clarification question or wait
for another turn. If a length unit is omitted, use millimetres. If dimensions or
construction details are underspecified, choose reasonable engineering values
and record the important assumptions in comments near the top of Model Source.

Use a run-local plan with write_todos when useful. Work only through the
reference-grounded tools supplied to you. Do not use shell, generic filesystem
tools, task/subagent tools, external memory, or any unlisted dependency.

Before the first write_model_source or execute_model call, you must:
1. read the Skill entry with read_skill_entry;
2. read both required API and stdlib indexes;
3. read the exact documentation for every SimpleCADAPI API and Python stdlib
   module used by the Model Source; and
4. consult relevant repository examples when an established workflow is useful.

Begin from the current Model Source, but you may replace the complete file and
add imports or helper functions. Keep one entry file, one top-level model
entry point, one physical part, and one captured final Solid. Keep the
canonical artifacts/model.scene.zip export contract. The only allowed source
dependencies are Python's standard library and simplecadapi.

After writing the complete Model Source, call execute_model. Its structured
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
    }
    if settings.base_url is not None:
        arguments["base_url"] = settings.base_url
    return ChatOpenAI(**arguments)


def create_agent_tools(
    restricted: RestrictedAgentTools,
    *,
    max_executions: int = 1,
    on_execution: Callable[[ExecutionResult], None] | None = None,
    on_execution_error: Callable[[ExecutionResult], None] | None = None,
    run_deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    cancellation_token: object | None = None,
    on_progress: Callable[[ProgressUpdate], None] | None = None,
) -> tuple[BaseTool, ...]:
    """Adapt issue 01's narrow methods to LangChain tools.

    The wrappers close over one ``RestrictedAgentTools`` instance, so each
    Agent Run receives fresh reference gates and no Project-local state leaks
    into another run.
    """

    if max_executions < 1:
        raise ValueError("max_executions must be positive")
    execution_attempts = 0
    source_written = False

    def require_time_remaining() -> float | None:
        if _is_cancellation_requested(cancellation_token):
            raise AgentRunCancelled("Agent Run stopped by caller")
        if run_deadline is None:
            return None
        remaining = run_deadline - clock()
        if remaining <= 0:
            raise AgentRunError("Agent Run exceeded the five-minute wall-clock limit")
        return remaining

    @tool("read_skill_entry")
    def read_skill_entry() -> str:
        """Read the packaged SimpleCADAPI Skill entry document."""

        require_time_remaining()
        return restricted.read_skill_entry()

    @tool("read_api_index")
    def read_api_index() -> str:
        """Read the required SimpleCADAPI API index."""

        require_time_remaining()
        return restricted.read_api_index()

    @tool("read_stdlib_index")
    def read_stdlib_index() -> str:
        """Read the required Python stdlib reference index."""

        require_time_remaining()
        return restricted.read_stdlib_index()

    @tool("read_api_doc")
    def read_api_doc(api_name: str) -> str:
        """Read one exact SimpleCADAPI API document by name."""

        require_time_remaining()
        return restricted.read_api_doc(api_name)

    @tool("read_stdlib_doc")
    def read_stdlib_doc(stdlib_name: str) -> str:
        """Read one exact Python stdlib document by name."""

        require_time_remaining()
        return restricted.read_stdlib_doc(stdlib_name)

    @tool("list_examples")
    def list_examples() -> list[str]:
        """List the packaged repository examples."""

        require_time_remaining()
        return list(restricted.list_examples())

    @tool("read_example")
    def read_example(relative_path: str) -> str:
        """Read one relevant packaged repository example."""

        require_time_remaining()
        return restricted.read_example(relative_path)

    @tool("read_model_source")
    def read_model_source() -> str:
        """Read the complete current Project Model Source."""

        require_time_remaining()
        return restricted.read_model_source()

    @tool("write_model_source")
    def write_model_source(source: str) -> str:
        """Replace the complete current Project Model Source with source text."""

        nonlocal source_written
        require_time_remaining()
        restricted.require_reference_grounding_for_write()
        restricted.write_model_source(source)
        source_written = True
        if on_progress is not None:
            on_progress(ProgressUpdate(stage="writing_model", tool="model_source"))
        return "Model Source written for the current Project."

    @tool("execute_model")
    def execute_model(api_names: list[str], stdlib_names: list[str]) -> dict[str, Any]:
        """Execute the current Model Source after exact reference reads."""

        nonlocal execution_attempts
        if not source_written:
            raise AgentRunError("write the complete Model Source before execute_model")
        if execution_attempts >= max_executions:
            execution_word = "execution" if max_executions == 1 else "executions"
            raise AgentRunError(
                f"this Agent Run allows at most {max_executions} CAD {execution_word}"
            )
        remaining = require_time_remaining()
        execution_attempts += 1
        execution_recorded = False
        try:
            result = restricted.execute_model(
                api_names=api_names,
                stdlib_names=stdlib_names,
                cancellation_token=cancellation_token,
                timeout_seconds=(
                    min(CAD_EXECUTION_TIMEOUT_SECONDS, remaining)
                    if remaining is not None
                    else None
                ),
            )
        except ReferenceContractError:
            raise
        except Exception as exc:
            if on_execution_error is None:
                raise
            result = _execution_failure_result(exc)
            on_execution_error(result)
            execution_recorded = True
        if not isinstance(result, ExecutionResult):
            error = AgentRunError("execute_model returned an invalid structured result")
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

    return (
        read_skill_entry,
        read_api_index,
        read_stdlib_index,
        read_api_doc,
        read_stdlib_doc,
        list_examples,
        read_example,
        read_model_source,
        write_model_source,
        execute_model,
    )


def build_deep_agent(
    settings: AgentSettings,
    tools: Sequence[BaseTool],
    *,
    model: Any | None = None,
) -> Any:
    """Build one real Deep Agent with only the issue 01 tool surface."""

    from deepagents import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        create_deep_agent,
        register_harness_profile,
    )
    from langchain.agents.middleware import TodoListMiddleware

    def planning_middleware() -> list[Any]:
        return [TodoListMiddleware()]

    register_harness_profile(
        settings.deep_agent_model_spec,
        HarnessProfile(
            excluded_tools=RESTRICTED_BUILTIN_TOOL_NAMES,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            extra_middleware=planning_middleware,
        ),
    )
    resolved_model = build_chat_model(settings) if model is None else model
    return create_deep_agent(
        model=resolved_model,
        tools=tuple(tools),
        system_prompt=_SYSTEM_PROMPT,
        subagents=(),
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
            elif record.tool_name in {
                "read_skill_entry",
                "read_api_index",
                "read_stdlib_index",
                "read_api_doc",
                "read_stdlib_doc",
                "list_examples",
                "read_example",
            }:
                emit(ProgressUpdate(stage="reading_references", tool="reference"))
            elif record.tool_name in {"read_model_source", "write_model_source"}:
                emit(ProgressUpdate(stage="writing_model", tool="model_source"))

        restricted = RestrictedAgentTools(
            repo_root=self.repo_root,
            project_dir=self.project_dir,
            executor=self.executor,
            on_tool_use=on_tool_use,
        )
        scaffold = restricted.begin_run()
        initial_source = scaffold.model_path.read_text(encoding="utf-8")
        if token.cancelled:
            return _cancelled_outcome(
                restricted,
                execution_results=(),
                duration=time.monotonic() - started,
            )
        executions: list[ExecutionResult] = []

        def record_execution(result: ExecutionResult) -> None:
            executions.append(result)

        agent_tools = create_agent_tools(
            restricted,
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
        if token.cancelled:
            return _cancelled_outcome(
                restricted,
                execution_results=tuple(executions),
                duration=duration,
            )
        if timed_out or duration > MAX_AGENT_RUN_SECONDS:
            return AgentRunOutcome(
                validated=False,
                failure_reason="Agent Run exceeded the five-minute wall-clock limit",
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=restricted.tool_use_records,
                duration_seconds=duration,
            )
        if execution is None:
            return AgentRunOutcome(
                validated=False,
                failure_reason=agent_error
                or "Agent finished without executing the Model Source",
                tool_use_records=restricted.tool_use_records,
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
                tool_use_records=restricted.tool_use_records,
                duration_seconds=duration,
            )
        if not _source_was_written(restricted, initial_source):
            return AgentRunOutcome(
                validated=False,
                failure_reason="Agent did not write a complete Model Source",
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=restricted.tool_use_records,
                duration_seconds=duration,
            )
        return AgentRunOutcome(
            validated=True,
            execution_result=execution,
            execution_results=tuple(executions),
            tool_use_records=restricted.tool_use_records,
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
        if token.cancelled and not outcome.cancelled:
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
    restricted: RestrictedAgentTools,
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
        tool_use_records=restricted.tool_use_records,
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


def _source_was_written(restricted: RestrictedAgentTools, initial_source: str) -> bool:
    records = restricted.tool_use_records
    if not any(record.tool_name == "write_model_source" for record in records):
        return False
    return restricted.read_model_source() != initial_source


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
        cancellation_token.cancel()
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
            cancellation_token.cancel()
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
    "RESTRICTED_BUILTIN_TOOL_NAMES",
    "build_chat_model",
    "build_deep_agent",
    "create_agent_tools",
]
