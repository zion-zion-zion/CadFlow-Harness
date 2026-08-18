"""Reference-grounded Deep Agent orchestration for one CAD generation run."""

from __future__ import annotations

import asyncio
import inspect
import importlib.metadata
import math
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from langchain_core.tools import BaseTool, tool

from .agent_logging import AgentRunLog
from .agent_backend import create_agent_backend
from .cad_executor import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    CancellationToken,
    ExecutionResult,
    PreviewFrame,
    redact_credentials,
)
from .contracts import ToolUseRecord
from .events import ProgressUpdate
from .harnesses import AgentHarness
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


MAX_AGENT_RUN_SECONDS = 10 * 60.0
AGENT_RUN_TIMEOUT_SECONDS = MAX_AGENT_RUN_SECONDS
MAX_PROVIDER_RETRIES = 2

try:
    DEEPAGENTS_IMPLEMENTATION_VERSION = importlib.metadata.version("deepagents")
except importlib.metadata.PackageNotFoundError:
    DEEPAGENTS_IMPLEMENTATION_VERSION = "unknown"

_AGENT_RUN_TIMEOUT_MESSAGE = "Agent Run exceeded the ten-minute wall-clock limit"
AGENT_EXCLUDED_TOOLS = frozenset({"execute", "delete", "task"})
AGENT_FILESYSTEM_TOOLS = (
    "read_file",
    "write_file",
    "edit_file",
    "ls",
    "glob",
    "grep",
)

_AGENT_TOOL_DESCRIPTION_OVERRIDES = {
    "grep": (
        "Search for a literal text pattern in the current Project or the "
        "read-only Skill reference. Shell commands and regular "
        "expressions are unavailable; run separate literal searches instead."
    ),
}


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
    token_usage: dict[str, int] | None = None
    cancelled: bool = False
    harness: AgentHarness = AgentHarness.DEEPAGENTS
    implementation_version: str | None = DEEPAGENTS_IMPLEMENTATION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "harness", AgentHarness(self.harness))
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
            "harness": self.harness.value,
            "implementation_version": self.implementation_version,
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
            "token_usage": self.token_usage,
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


_SYSTEM_PROMPT = """You are the primary Text-to-CAD Agent for this run.

## Current run contract

The current executor accepts one final `cad.Shape` containing exactly one
positive-volume solid and creates the canonical `artifacts/model.scene.zip`.
This is the contract for the current run, not a general limitation of CadFlow
or of future CadFlowAgent capabilities.

The user's request defines the desired geometry. Skills provide implementation
guidance. Skills and Agent preferences must not change the current executor
contract.

## Request policy

Treat the user's request as complete and do not wait for another turn.

Infer non-critical parameters when needed, use millimetres when no length unit
is given, and record important inferred assumptions near the top of the Model
Source.

Do not invent, remove, or alter user-critical requirements such as the part
type, topology, required holes, major dimensions, or requested features.

## Planning phase

Turn each request into two separate planning artifacts: a short Request Spec
that records what the request means, and a todo list that records the actions
needed to implement it. Keep the Request Spec and todo list distinct.

Complete this read-only discovery before planning the implementation:

1. Inspect the current `model.py` and the available Skill metadata.
2. Select the Skills whose descriptions match the request. Read each selected
   full `SKILL.md`, then read only the references needed for this request.
   Skill metadata is an index, not a substitute for the full instructions.
3. Emit a concise normal assistant text block using this exact shape:

   ```text
   SPEC
   Intent: <the requested result>
   Hard requirements:
   - <user-critical requirement>
   Constraints:
   - <current-task or technical constraint>
   Assumptions:
   - <inferred non-critical value, or "None">
   Skill guidance:
   - <selected Skill>: <the applicable rule or API boundary>
   ```

Do not add an acceptance-criteria section to the Request Spec. The fixed run
contract and the structured `validate_model` result remain authoritative.

Immediately after the SPEC, call `write_todos` with 3-6 high-level execution
steps. Todo items track actions and status only; do not copy the full SPEC into
them. Mark completed read-only discovery as completed and the current
implementation step as in progress. Keep the todo list current as work moves
forward.

If validation changes an assumption or reveals a conflict with the request or
the current run contract, emit a normal assistant text block beginning with
`SPEC UPDATE` containing only the changed sections, then update the todo list.
Do not rewrite the SPEC for an ordinary implementation or API error.

## Working method

Use only the tools exposed for this run. Their actual permissions and
filesystem boundaries are authoritative.

After the Planning phase, the current `model.py` may be empty or may contain
an existing implementation. Create, preserve, or repair the required
`build_model(model: cad.Model) -> cad.Shape` entry point.

If Skills disagree, preserve the current run contract and choose the narrowest
compatible guidance.

Use only public CadFlow and Python APIs. Do not import private CadFlow engine
modules, OCP types, native handles, or private shared-library symbols.

Before making any change to `model.py` or a local Python helper module, the
initial SPEC and todo plan must have been produced. This applies even when the
request appears simple. Do not write or edit Project source before the initial
todo plan exists.

## Implementation and validation loop

Implement the requested geometry in the Project Model Source and local Python
modules.

After the Model Source is complete, call `validate_model`. Treat its
structured result as the source of truth. Inspect the reported status, failure
type, location, preflight result, imported modules, geometry facts, Scene
Artifact status, and diagnostic output.

When validation fails:

1. Identify the reported failure and its likely cause.
2. Make a concrete, material source change that addresses that failure.
3. Preserve the user's requirements and the current run contract.
4. Call `validate_model` again only after the source has materially changed.

Never retry an unchanged or semantically equivalent Model Source. Do not make
unrelated changes merely to continue the run.

If the requested result cannot satisfy the current run contract, stop with a
failure rather than silently changing the requested geometry or output type.

When validation succeeds, stop all further tool calls immediately. Do not edit
the Model Source, revalidate, or perform cleanup after a Validated Result.
"""


def _build_agent_system_prompt(
    *,
    workspace_root: str | Path,
    skill_root: str | Path | None,
) -> str:
    """Add the concrete run-local filesystem boundaries to the Agent prompt."""

    project_dir = Path(workspace_root).expanduser().resolve()
    read_only_roots = (
        [Path(skill_root).expanduser().resolve()] if skill_root is not None else []
    )
    read_only_lines = "\n".join(f"- `{root}`" for root in read_only_roots)
    if not read_only_lines:
        read_only_lines = "- None"
    return (
        _SYSTEM_PROMPT
        + f"""

## Filesystem boundaries for this run

The current Project workspace is exactly:
`{project_dir}`

Treat that directory as the working directory and the base for every relative
path. You may read Project files, but create or edit only model.py and local
Python helper modules inside that directory.
The required Model Source is `{project_dir / "model.py"}`.
Do not search parent directories, sibling directories, or other Projects to
locate it.

The following directory is a read-only Skill reference outside the Project
workspace:
{read_only_lines}

You may list, search, and read files under that Skill reference, but must never
create, edit, rename, or delete anything there. Do not inspect or modify files
elsewhere. File tools are the only workspace mutation interface; use them only
with the Project or the read-only Skill reference above.
"""
    )


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
    on_execution: Callable[[ExecutionResult], None] | None = None,
    on_execution_error: Callable[[ExecutionResult], None] | None = None,
    run_deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    cancellation_token: object | None = None,
    on_progress: Callable[[ProgressUpdate], None] | None = None,
) -> tuple[BaseTool, ...]:
    """Expose one zero-argument structured CAD validation tool."""

    execution_attempts = 0

    def require_time_remaining() -> float | None:
        if _is_cancellation_requested(cancellation_token):
            raise AgentRunCancelled("Agent Run stopped by caller")
        if run_deadline is None:
            return None
        remaining = run_deadline - clock()
        if remaining <= 0:
            raise AgentRunError(_AGENT_RUN_TIMEOUT_MESSAGE)
        return remaining

    @tool("validate_model")
    def validate_model() -> dict[str, Any]:
        """Run and structurally validate the current model.py and Scene Artifact."""

        nonlocal execution_attempts
        remaining = require_time_remaining()
        execution_attempts += 1
        execution_recorded = False

        def report_preview(frame: PreviewFrame) -> None:
            if on_progress is None:
                return
            on_progress(
                ProgressUpdate(
                    stage="preview_ready",
                    tool="cad",
                    attempt=execution_attempts,
                    result=f"{frame.operation} preview",
                    preview_attempt=frame.attempt,
                    preview_revision=frame.revision,
                    preview_operation=frame.operation,
                )
            )

        try:
            result = validator.validate_model(
                cancellation_token=cancellation_token,
                timeout_seconds=(
                    min(CAD_EXECUTION_TIMEOUT_SECONDS, remaining)
                    if remaining is not None
                    else None
                ),
                attempt=execution_attempts,
                preview_callback=report_preview if on_progress is not None else None,
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
    from deepagents.middleware import FilesystemMiddleware
    from langchain.agents.middleware import TodoListMiddleware

    def planning_middleware() -> list[Any]:
        return [TodoListMiddleware()]

    profile = HarnessProfile(
        excluded_tools=AGENT_EXCLUDED_TOOLS,
        tool_description_overrides=_AGENT_TOOL_DESCRIPTION_OVERRIDES,
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        extra_middleware=planning_middleware,
    )
    register_harness_profile(settings.deep_agent_model_spec, profile)
    register_harness_profile(settings.provider, profile)
    resolved_model = build_chat_model(settings) if model is None else model
    resolved_workspace_root = Path(workspace_root or Path.cwd()).expanduser().resolve()
    resolved_skill_root = (
        Path(skill_root).expanduser().resolve() if skill_root is not None else None
    )
    backend = create_agent_backend(
        resolved_workspace_root,
        skill_root=resolved_skill_root,
        shell_timeout=int(CAD_EXECUTION_TIMEOUT_SECONDS),
    )
    return create_deep_agent(
        model=resolved_model,
        tools=tuple(tools),
        system_prompt=_build_agent_system_prompt(
            workspace_root=resolved_workspace_root,
            skill_root=resolved_skill_root,
        ),
        middleware=(
            FilesystemMiddleware(
                backend=backend,
                custom_tool_descriptions=_AGENT_TOOL_DESCRIPTION_OVERRIDES,
                tools=list(AGENT_FILESYSTEM_TOOLS),
            ),
        ),
        subagents=(),
        skills=[str(resolved_skill_root)] if resolved_skill_root is not None else None,
        backend=backend,
        memory=None,
        checkpointer=None,
        store=None,
        name="text-to-cad-primary",
    )


class ReferenceGroundedAgent:
    """Run one primary Deep Agent within a wall-clock deadline."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        repo_root: str | Path,
        project_dir: str | Path,
        executor: Any | None = None,
        model: Any | None = None,
        run_log: AgentRunLog | None = None,
    ) -> None:
        self.settings = settings
        self.repo_root = repo_root
        self.project_dir = project_dir
        self.executor = executor
        self.model = model
        self.run_log = run_log

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
            if (
                self.run_log is not None
                and record.tool_name == "prepare_model_source"
            ):
                self.run_log.record_internal_tool(record.tool_name, record.target)
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
                run_log=self.run_log,
            )
        except Exception as exc:  # Agent/provider errors become a safe diagnosis.
            agent_error = _safe_failure_reason(exc)

        duration = time.monotonic() - started
        execution = executions[-1] if executions else None
        if timed_out or token.cancellation_reason == "timeout" or duration > MAX_AGENT_RUN_SECONDS:
            return AgentRunOutcome(
                validated=False,
                failure_reason=_AGENT_RUN_TIMEOUT_MESSAGE,
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
        run_log = AgentRunLog(
            self.store.project_directory(project.project_id),
            harness=AgentHarness.DEEPAGENTS.value,
            implementation_version=DEEPAGENTS_IMPLEMENTATION_VERSION,
        )
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
            run_log.finish(
                status=outcome.status,
                failure_reason=outcome.failure_reason,
            )
            return outcome
        try:
            settings = self.settings_factory()
            run_log.configure(
                provider=settings.provider,
                model_id=settings.model_id,
                base_url=settings.base_url,
            )
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
            run_log.finish(
                status=outcome.status,
                failure_reason=outcome.failure_reason,
            )
            return outcome

        try:
            factory_kwargs: dict[str, Any] = {
                "settings": settings,
                "repo_root": self.repo_root,
                "project_dir": self.store.project_directory(project.project_id),
            }
            try:
                factory_parameters = inspect.signature(self.agent_factory).parameters
            except (TypeError, ValueError):
                factory_parameters = {}
            if (
                "run_log" in factory_parameters
                or any(
                    item.kind is inspect.Parameter.VAR_KEYWORD
                    for item in factory_parameters.values()
                )
            ):
                factory_kwargs["run_log"] = run_log
            agent = self.agent_factory(**factory_kwargs)
            outcome = _invoke_agent_run(
                agent,
                project.prompt or prompt,
                cancellation_token=token,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
        if run_log.token_usage is not None:
            outcome = replace(outcome, token_usage=run_log.token_usage)
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
            run_log.finish(
                status=outcome.status,
                failure_reason=outcome.failure_reason,
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
        run_log.finish(
            status=outcome.status,
            failure_reason=outcome.failure_reason,
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
        and result.final_shape_count == 1
        and result.solid_count == 1
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


def _invoke_agent_with_deadline(
    agent: Any,
    prompt: str,
    *,
    deadline: float,
    cancellation_token: CancellationToken,
    run_log: AgentRunLog | None = None,
) -> tuple[str | None, bool]:
    """Invoke the primary Agent and cancel its task on Stop or timeout."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        cancellation_token.cancel(reason="timeout")
        return _AGENT_RUN_TIMEOUT_MESSAGE, True

    async def invoke() -> tuple[str | None, bool]:
        config: dict[str, Any] = {}
        if run_log is not None:
            config["callbacks"] = [run_log.callback_handler()]
        task = asyncio.create_task(
            agent.ainvoke(
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
                },
                config=config or None,
            )
        )
        try:
            while not task.done():
                if cancellation_token.cancelled:
                    task.cancel()
                    await _await_cancelled_task(task)
                    timed_out = cancellation_token.cancellation_reason == "timeout"
                    return (
                        _AGENT_RUN_TIMEOUT_MESSAGE
                        if timed_out
                        else "Agent Run stopped by caller",
                        timed_out,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cancellation_token.cancel(reason="timeout")
                    task.cancel()
                    await _await_cancelled_task(task)
                    return _AGENT_RUN_TIMEOUT_MESSAGE, True
                await asyncio.wait({task}, timeout=min(0.05, remaining))
            try:
                task.result()
            except Exception as exc:
                return _safe_failure_reason(exc), False
            return None, False
        finally:
            if not task.done():
                task.cancel()
                await _await_cancelled_task(task)

    return asyncio.run(invoke())


async def _await_cancelled_task(task: asyncio.Task[Any]) -> None:
    """Wait until Agent cancellation has propagated through LangGraph cleanup."""

    try:
        await task
    except asyncio.CancelledError:
        pass


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
    "MAX_PROVIDER_RETRIES",
    "ReferenceGroundedAgent",
    "build_chat_model",
    "build_deep_agent",
    "create_agent_tools",
]
