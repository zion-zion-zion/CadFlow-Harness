"""Reference-grounded Deep Agent orchestration for one CAD generation run."""

from __future__ import annotations

import asyncio
import inspect
import importlib.metadata
import math
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence, cast

from langchain_core.tools import BaseTool, tool

from .agent_logging import ConversationLog
from .agent_backend import create_agent_backend
from .cad_executor import (
    CAD_EXECUTION_TIMEOUT_SECONDS,
    CancellationToken,
    ExecutionResult,
    redact_credentials,
)
from .cad_review import ReviewResult, review_cad
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

ReasoningEffort = Literal["none", "low", "medium", "high", "max"]
ReasoningSummary = Literal["auto", "concise", "detailed"]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none",
    "low",
    "medium",
    "high",
    "max",
)
REASONING_SUMMARIES: tuple[ReasoningSummary, ...] = (
    "auto",
    "concise",
    "detailed",
)


@dataclass(frozen=True)
class AgentSettings:
    """Backend-only configuration for the single OpenAI-compatible model."""

    model_id: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    provider: str = "openai"
    reasoning_effort: ReasoningEffort | None = None
    reasoning_summary: ReasoningSummary | None = None

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
        reasoning_effort = values.get("OPENAI_REASONING_EFFORT", "").strip()
        if reasoning_effort and reasoning_effort not in REASONING_EFFORTS:
            allowed = ", ".join(REASONING_EFFORTS)
            raise AgentConfigurationError(
                f"OPENAI_REASONING_EFFORT must be one of: {allowed}"
            )
        reasoning_summary = values.get("OPENAI_REASONING_SUMMARY", "").strip()
        if reasoning_summary and reasoning_summary not in REASONING_SUMMARIES:
            allowed = ", ".join(REASONING_SUMMARIES)
            raise AgentConfigurationError(
                f"OPENAI_REASONING_SUMMARY must be one of: {allowed}"
            )
        return cls(
            model_id=values["OPENAI_MODEL_ID"].strip(),
            api_key=values["OPENAI_API_KEY"],
            base_url=base_url,
            reasoning_effort=(
                cast(ReasoningEffort, reasoning_effort)
                if reasoning_effort
                else None
            ),
            reasoning_summary=(
                cast(ReasoningSummary, reasoning_summary)
                if reasoning_summary
                else None
            ),
        )

    @property
    def deep_agent_model_spec(self) -> str:
        return f"{self.provider}:{self.model_id}"

    @property
    def use_responses_api(self) -> bool:
        """Use Chat Completions only when reasoning is explicitly disabled."""

        return self.reasoning_effort != "none"

    @property
    def reasoning_parameters(self) -> dict[str, str] | None:
        """Return Responses-only reasoning options, when Responses is active."""

        if not self.use_responses_api:
            return None
        parameters: dict[str, str] = {}
        if self.reasoning_effort is not None:
            parameters["effort"] = self.reasoning_effort
        if self.reasoning_summary is not None:
            parameters["summary"] = self.reasoning_summary
        return parameters or None


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
    review_result: ReviewResult | None = None

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
            "review_result": (
                self.review_result.to_dict() if self.review_result is not None else None
            ),
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

## Working method

Use only the tools exposed for this run. Their actual permissions and
filesystem boundaries are authoritative.

First inspect the current `model.py`. It may be empty or may contain an
existing implementation. Create, preserve, or repair the required
`build_model(model: cad.Model) -> cad.Shape` entry point.

Read any relevant CadFlow Skills and their references when they help with the
request. You may choose more than one Skill. If Skills disagree, preserve the
current run contract and choose the narrowest compatible guidance.

Use only public CadFlow and Python APIs. Do not import private CadFlow engine
modules, OCP types, native handles, or private shared-library symbols.

Before making any change to `model.py` or a local Python helper module, you
must call `write_todos` and create a concise plan for this run. This is
required even when the request appears simple. Keep the plan current as the
work progresses: mark the active step, complete finished steps, and add or
adjust steps when validation reveals new work. Do not write or edit Project
source before the initial todo plan exists.

## Implementation and validation loop

Choose a workflow before implementing the requested geometry:

- For simple work, implement the complete Model Source and validate it once.
- For complex single-part work, use staged implementation and validation.

Use judgment rather than a fixed feature-count threshold. Multiple dependent
boolean feature groups, repeated features, and topology-sensitive finishing
operations such as fillets, chamfers, or shells are signals that staged work
will reduce risk.

For complex work, normally plan two to four materially distinct validation
stages in the todo list. Every stage must leave a runnable Model Source whose
`build_model` entry point returns exactly one positive-volume solid that is a
meaningful precursor of the requested final part. For a new part, progress
from the base solid through major additive or subtractive feature groups and
then topology-sensitive finishing features. For a complex change to an
existing implementation, preserve the current model and add requested feature
groups incrementally instead of rebuilding it without cause. Do not turn a
requested single part into an assembly merely to split the work into stages.

Call `validate_model` after each planned stage. A successful intermediate
validation is only a checkpoint: continue to the next planned stage without
calling `cad_review`. If an intermediate validation fails, repair that stage
and validate the material source change before adding later feature groups.
Never stack more features on a failed stage.

For every validation, treat the structured result as the source of truth.
Inspect the reported status, failure type, location, preflight result,
imported modules, geometry facts, Scene Artifact status, and diagnostic
output.

When validation fails:

1. Identify the reported failure and its likely cause.
2. Make a concrete, material source change that addresses that failure.
3. Preserve the user's requirements and the current run contract.
4. Call `validate_model` again only after the source has materially changed.

Never retry an unchanged or semantically equivalent Model Source. Do not make
unrelated changes merely to continue the run.

If the requested result cannot satisfy the current run contract, stop with a
failure rather than silently changing the requested geometry or output type.

When the complete requested geometry passes final validation, call
`cad_review` immediately. The review tool is a read-only quality gate and must
be called before you claim completion. If it returns `fail`, use its
structured findings to make a material Model Source change, then call
`validate_model` and `cad_review` again. Stop only after `cad_review` returns
`pass` or the request cannot be satisfied.
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
        "use_responses_api": settings.use_responses_api,
    }
    if settings.base_url is not None:
        arguments["base_url"] = settings.base_url
    if settings.reasoning_parameters is not None:
        arguments["reasoning"] = settings.reasoning_parameters
    elif settings.reasoning_effort is not None:
        arguments["reasoning_effort"] = settings.reasoning_effort
    return ChatOpenAI(**arguments)


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
    clock: Callable[[], float] = time.monotonic,
    cancellation_token: object | None = None,
    on_progress: Callable[[ProgressUpdate], None] | None = None,
) -> tuple[BaseTool, ...]:
    """Expose zero-argument CAD validation and review tools."""

    execution_attempts = 0
    latest_execution: ExecutionResult | None = None

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

        nonlocal execution_attempts, latest_execution
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
                attempt=execution_attempts,
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
        return result.to_dict()

    @tool("cad_review")
    def cad_review() -> dict[str, Any]:
        """Review the latest validated CAD model against the user's request.

        This tool is mandatory after the complete requested geometry passes
        its final validate_model call. Do not call it for successful
        intermediate checkpoints in a staged build. It returns a bounded
        pass/fail result with structured findings; it never edits model.py or
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
            validator.record_tool_use("cad_review", "model.py and .cad-review")
            try:
                source = (validator.project_dir / "model.py").read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                result = ReviewResult(
                    status="fail",
                    summary="CAD review could not read model.py.",
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
        conversation_log: ConversationLog | None = None,
    ) -> None:
        self.settings = settings
        self.repo_root = repo_root
        self.project_dir = project_dir
        self.executor = executor
        self.model = model
        self.conversation_log = conversation_log

    def run(
        self,
        prompt: str,
        *,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
        conversation_context: list[dict[str, str]] | None = None,
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
                self.conversation_log is not None
                and record.tool_name == "prepare_model_source"
            ):
                self.conversation_log.record_internal_tool(record.tool_name, record.target)
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
        review_results: list[ReviewResult] = []

        def record_execution(result: ExecutionResult) -> None:
            executions.append(result)

        def record_review(result: ReviewResult) -> None:
            review_results.append(result)
            emit(
                ProgressUpdate(
                    stage="reviewed",
                    tool="cad_review",
                    result=result.summary,
                )
            )

        agent_tools = create_agent_tools(
            validator,
            request_text=prompt,
            review_settings=self.settings,
            reviewer_callbacks=(
                [self.conversation_log.callback_handler()]
                if self.conversation_log is not None
                else None
            ),
            on_execution=record_execution,
            on_execution_error=record_execution,
            on_review=record_review,
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
                conversation_log=self.conversation_log,
                conversation_context=conversation_context,
            )
        except Exception as exc:  # Agent/provider errors become a safe diagnosis.
            agent_error = _safe_failure_reason(exc)

        duration = time.monotonic() - started
        execution = executions[-1] if executions else None
        review_result = review_results[-1] if review_results else None
        if timed_out or token.cancellation_reason == "timeout" or duration > MAX_AGENT_RUN_SECONDS:
            return AgentRunOutcome(
                validated=False,
                failure_reason=_AGENT_RUN_TIMEOUT_MESSAGE,
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=validator.tool_use_records,
                duration_seconds=duration,
                review_result=review_result,
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
                review_result=review_result,
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
                review_result=review_result,
            )
        if review_result is None:
            return AgentRunOutcome(
                validated=False,
                failure_reason=agent_error or "Agent finished without calling cad_review",
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=validator.tool_use_records,
                duration_seconds=duration,
            )
        if review_result.status != "pass":
            return AgentRunOutcome(
                validated=False,
                failure_reason=review_result.summary or "CAD review failed",
                execution_result=execution,
                execution_results=tuple(executions),
                tool_use_records=validator.tool_use_records,
                duration_seconds=duration,
                review_result=review_result,
            )
        return AgentRunOutcome(
            validated=True,
            execution_result=execution,
            execution_results=tuple(executions),
            tool_use_records=validator.tool_use_records,
            duration_seconds=duration,
            review_result=review_result,
        )


class AgentRunService:
    """Execute one turn within a durable Project conversation."""

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
        conversation_log: ConversationLog | None = None,
        turn_id: str | None = None,
    ) -> AgentRunOutcome:
        project = (
            self.store.get_project(project_id)
            if prompt_submitted
            else self.store.submit_prompt(project_id, prompt)
        )
        if prompt_submitted and project.state != ProjectState.RUNNING:
            raise AgentRunError("Agent Run requires a Running Project")
        token = cancellation_token or CancellationToken()
        owns_conversation = conversation_log is None
        active_turn_id = turn_id or uuid.uuid4().hex
        log = conversation_log or ConversationLog(
            self.store.project_directory(project.project_id),
            conversation_id=project.project_id,
            turn_id=active_turn_id,
            request_id=uuid.uuid4().hex,
            user_message=prompt,
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
            if owns_conversation:
                _finish_conversation_turn(log, outcome, self.store, project.project_id)
            return outcome
        try:
            settings = self.settings_factory()
            log.configure(
                provider=settings.provider,
                model_id=settings.model_id,
                base_url=settings.base_url,
                reasoning_effort=settings.reasoning_effort,
                reasoning_summary=settings.reasoning_summary,
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
            if owns_conversation:
                _finish_conversation_turn(log, outcome, self.store, project.project_id)
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
            accepts_factory_kwargs = any(
                item.kind is inspect.Parameter.VAR_KEYWORD
                for item in factory_parameters.values()
            )
            if (
                "conversation_log" in factory_parameters
                or accepts_factory_kwargs
            ):
                factory_kwargs["conversation_log"] = log
            agent = self.agent_factory(**factory_kwargs)
            conversation_context = log.context_messages(
                exclude_turn_id=active_turn_id
            )
            outcome = _invoke_agent_run(
                agent,
                project.prompt or prompt,
                cancellation_token=token,
                progress_callback=progress_callback,
                conversation_context=conversation_context,
            )
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
        if log.token_usage is not None:
            outcome = replace(outcome, token_usage=log.token_usage)
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
            if owns_conversation:
                _finish_conversation_turn(log, outcome, self.store, project.project_id)
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
        if owns_conversation:
            _finish_conversation_turn(log, outcome, self.store, project.project_id)
        return outcome


def _invoke_agent_run(
    agent: Any,
    prompt: str,
    *,
    cancellation_token: CancellationToken,
    progress_callback: Callable[[ProgressUpdate], None] | None,
    conversation_context: list[dict[str, str]] | None = None,
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
    if accepts_kwargs or "conversation_context" in parameters:
        kwargs["conversation_context"] = conversation_context
    return run(prompt, **kwargs)


def _finish_conversation_turn(
    log: ConversationLog,
    outcome: AgentRunOutcome,
    store: ProjectStore,
    project_id: str,
) -> None:
    assistant_message = log.latest_model_response_text()
    if not assistant_message:
        if outcome.validated:
            version = store.current_artifact_version(project_id)
            assistant_message = (
                f"CAD model updated successfully. Artifact v{version:04d} is ready."
                if version is not None
                else "CAD model updated successfully."
            )
        else:
            assistant_message = outcome.failure_reason or "The CAD turn failed."
    log.finish(
        status=outcome.status,
        failure_reason=outcome.failure_reason,
        assistant_message=assistant_message,
        artifact_version=(
            store.current_artifact_version(project_id) if outcome.validated else None
        ),
    )


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
    conversation_log: ConversationLog | None = None,
    conversation_context: list[dict[str, str]] | None = None,
) -> tuple[str | None, bool]:
    """Invoke the primary Agent and cancel its task on Stop or timeout."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        cancellation_token.cancel(reason="timeout")
        return _AGENT_RUN_TIMEOUT_MESSAGE, True

    async def invoke() -> tuple[str | None, bool]:
        config: dict[str, Any] = {}
        if conversation_log is not None:
            config["callbacks"] = [conversation_log.callback_handler()]
        messages: list[dict[str, str]] = list(conversation_context or ())
        messages.append(
            {
                "role": "user",
                "content": (
                    "Complete this CAD generation request in the current "
                    f"Project: {prompt}"
                ),
            }
        )
        task = asyncio.create_task(
            agent.ainvoke(
                {"messages": messages},
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
    "ReasoningEffort",
    "ReasoningSummary",
    "AGENT_RUN_TIMEOUT_SECONDS",
    "MAX_AGENT_RUN_SECONDS",
    "MAX_PROVIDER_RETRIES",
    "ReferenceGroundedAgent",
    "build_chat_model",
    "build_deep_agent",
    "create_agent_tools",
]
