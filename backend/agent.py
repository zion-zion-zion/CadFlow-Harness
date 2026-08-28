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

from .agent_logging import (
    PRIMARY_AGENT_ID,
    PRIMARY_AGENT_NAME,
    PRIMARY_AGENT_ROLE,
    ConversationLog,
)
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


AGENT_RUN_TIMEOUT_ENV_VAR = "CADFLOW_AGENT_RUN_TIMEOUT_SECONDS"
REVIEW_MODEL_ENV_VAR = "OPENAI_REVIEW_MODEL_ID"
DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 20 * 60.0
# Backward-compatible names for callers that need the default budget. Runtime
# runs use AgentSettings.run_timeout_seconds so repository-local .env values
# are resolved after dotenv loading.
MAX_AGENT_RUN_SECONDS = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
AGENT_RUN_TIMEOUT_SECONDS = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
MAX_PROVIDER_RETRIES = 2

try:
    DEEPAGENTS_IMPLEMENTATION_VERSION = importlib.metadata.version("deepagents")
except importlib.metadata.PackageNotFoundError:
    DEEPAGENTS_IMPLEMENTATION_VERSION = "unknown"

AGENT_EXCLUDED_TOOLS = frozenset({"execute", "task"})
AGENT_FILESYSTEM_TOOLS = (
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "ls",
)

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


def resolve_agent_run_timeout_seconds(
    environment: Mapping[str, str] | None = None,
) -> float:
    """Resolve the positive per-run wall-clock budget from the environment."""

    values = os.environ if environment is None else environment
    raw = values.get(AGENT_RUN_TIMEOUT_ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    try:
        timeout_seconds = float(raw)
    except ValueError as error:
        raise AgentConfigurationError(
            f"{AGENT_RUN_TIMEOUT_ENV_VAR} must be a finite positive number of seconds"
        ) from error
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise AgentConfigurationError(
            f"{AGENT_RUN_TIMEOUT_ENV_VAR} must be a finite positive number of seconds"
        )
    return timeout_seconds


def _format_timeout_seconds(timeout_seconds: float) -> str:
    return f"{timeout_seconds:g}"


def _agent_run_timeout_message(timeout_seconds: float) -> str:
    return (
        "Agent Run exceeded the configured "
        f"{_format_timeout_seconds(timeout_seconds)}-second wall-clock limit"
    )


@dataclass(frozen=True)
class AgentSettings:
    """Backend-only configuration for the Agent and optional CAD reviewer."""

    model_id: str
    api_key: str = field(repr=False)
    base_url: str | None = None
    provider: str = "openai"
    reasoning_effort: ReasoningEffort | None = None
    reasoning_summary: ReasoningSummary | None = None
    run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    review_model_id: str | None = None

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
            run_timeout_seconds=resolve_agent_run_timeout_seconds(values),
            review_model_id=values.get(REVIEW_MODEL_ENV_VAR, "").strip() or None,
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

The stable entry point is
`build_model(model: cad.Model) -> cad.Shape | cad.Assembly`.

Return a `cad.Shape` only when the requested product is one separately
manufactured rigid part. It must contain exactly one valid positive-volume
solid. Return a semantic `cad.Assembly` when the product has multiple
separately manufactured parts, repeated instances, or nested subassemblies.
Every Assembly leaf must be a valid one-solid `cad.Part`; preserve reusable
Part identity, unique component IDs, connectors, constraints, and nesting.
Never fuse multiple parts into a Shape as a substitute for an Assembly.

The executor creates a complete Draft product bundle after the returned value
passes the early Assembly gates. It checks the semantic structure, strict constraint solve and every residual,
STEP replay, Scene parsing, product envelope, and current-pose collision at a
maximum allowed penetration of 0.02 mm. The host promotes a deterministically
Passed Draft to Accepted only after independent `cad_review` also passes.
When an early Assembly gate fails, `validate_model` returns a diagnostic Draft
with `validation_short_circuited=true`. Its missing product bundle, Scene, and
review evidence are intentional because downstream export work was skipped.
Repair the reported failed check; do not treat those absent downstream
artifacts as another source defect. The next early-gate pass performs the full
export and replay checks.
Write Python source only; the executor owns Scene, STEP, BOM, validation,
assumption, semantic-model, and source-snapshot artifacts.
Use `product_validation_checks` for solve diagnosis. Return the semantic product
normally; do not leave temporary solve, inspection, or debug-print probes in
the final source merely to repeat host validation.

The user's request defines the desired geometry. Skills provide implementation
guidance. Skills and Agent preferences must not change the current executor
contract.

## Request policy

Treat the user's request as complete. Work autonomously and do not wait for
human approval between planning, implementation, validation, and repair. The
whole run has a configured wall-clock budget of
__CADFLOW_AGENT_RUN_TIMEOUT_SECONDS__ seconds.

Infer non-critical parameters when needed, use millimetres when no length unit
is given, and record important inferred assumptions in `PRODUCT_SPEC`.

Do not invent, remove, or alter user-critical requirements such as the part
type, topology, required holes, major dimensions, or requested features.

## Working method

Use only the tools exposed for this run. Their actual permissions and
filesystem boundaries are authoritative.

First inspect the current `/code/model.py`. It may be empty or may contain an
existing implementation. Inspect relevant local helper modules too, then
create, preserve, or repair the stable `build_model` entry point.

For a complex product, split `/code/` into focused Python modules. Keep shared
dimensions and physical equations in one source of truth, Part families in
component modules, Assembly construction and constraints in an assembly
module, and `model.py` as the small orchestration entry point. Reuse one Part
definition for repeated instances. Remove obsolete helper modules when a
repair makes them misleading or unreachable.

Read any relevant CadFlow Skills and their references when they help with the
request. You may choose more than one Skill. If Skills disagree, preserve the
current run contract and choose the narrowest compatible guidance.

Use only public CadFlow and Python APIs. Do not import private CadFlow engine
modules, OCP types, native handles, or private shared-library symbols.

For Assembly Part bodies, use replayable constructors and booleans consistently:
`cad.make_*_rsolid`, `cad.union_rsolid`, and `cad.cut_rsolid` produce or consume
`cad.Solid`. Keep each result connected and position occurrences with Assembly
placements. The separate `cad.Model` DSL returns `cad.Shape`; do not pass a
`model.box`, `model.cylinder`, or `model.translate` result to `make_part_rpart`
or replayable solid booleans.

Every Assembly source must define a JSON-compatible product contract like:

```python
PRODUCT_SPEC = {
    "assumptions": ["Named non-critical assumption"],
    "envelope": {"max_size_mm": [200.0, 160.0, 120.0]},
    "collision_exclusions": [
        {
            "component_a": "assembly/component_a",
            "component_b": "assembly/component_b",
            "reason": "Pair-specific physical reason",
        }
    ],
}
```

Use the actual full leaf component paths reported by validation. An exclusion
applies to one pair only and requires a concrete physical justification. An
empty exclusion list is preferred when no intentional contact or fit exists.

Before making any change to `/code/model.py` or a local Python helper module, you
must call `write_todos` and create a concise plan for this run. This is
required even when the request appears simple. Keep the plan current as the
work progresses: mark the active step, complete finished steps, and add or
adjust steps when validation reveals new work. Do not write or edit Project
source before the initial todo plan exists.

## Implementation and validation loop

Choose a workflow before implementing the requested geometry:

- For simple work, implement the complete Model Source and validate it once.
- For complex single-part work, use staged implementation and validation.
- For complex Assembly work, stage shared dimensions, unique Part families,
  subassemblies, and the final constrained product.

Use judgment rather than a fixed feature-count threshold. Multiple dependent
boolean feature groups, repeated features, and topology-sensitive finishing
operations such as fillets, chamfers, or shells are signals that staged work
will reduce risk.

For complex work, normally plan two to four materially distinct validation
stages in the todo list. Every stage must leave a runnable, deterministically
valid product candidate. A single-part stage returns one meaningful
positive-volume precursor Shape. An Assembly stage returns a coherent partial
semantic Assembly whose current leaf Parts, IDs, solve, envelope, and collision
checks pass. Preserve a requested single part as a Shape; staging alone is not
a reason to create an Assembly. For changes to existing source, retain working
behavior and add requested feature or component groups incrementally.

Call `validate_model` after each planned stage. A successful intermediate
validation is only a checkpoint: continue to the next planned stage without
calling `cad_review`. If an intermediate validation fails, repair that stage
and validate the material source change before adding later feature groups.
Never stack more features on a failed stage.

A passing candidate is intermediate only when at least one explicit user
requirement is still absent from its source. When every explicit requirement is
implemented, treat the first deterministic pass as final: stop polishing,
comment-only cleanup, and unrequested detail, then call `cad_review` in the same
turn. Leave enough of the reported run budget to complete the final review.

For every validation, treat the structured result as the source of truth.
Inspect the reported status, failure type, location, preflight result,
imported modules, geometry facts, Scene Artifact status, and diagnostic
output. For product validation, also inspect `result_kind`, component and Part
counts, `product_status`, `product_validation_status`, and every
`product_validation_failure`. Inspect `product_validation_checks` for the
failed check's solve error, residual IDs, collision pairs and contacts, or
envelope measurements before choosing a repair.
A successful subprocess can still be a Draft with blocking validation failures.
For a short-circuited diagnostic Draft, prioritize its failed validation check
and ignore the intentionally absent downstream artifacts.

When validation fails:

1. Identify the reported failure and its likely cause.
2. Make a concrete, material source change that addresses that failure.
3. Preserve the user's requirements and the current run contract.
4. Call `validate_model` again only after the source has materially changed.

For a timeout, use `execution_phase` and the phase named in `error`. The next
source revision must reduce work in that phase rather than add unrelated
detail. When changing shared helpers across files, finish provider definitions
and reconcile every importer before validating the candidate.

Never retry an unchanged or semantically equivalent Model Source. Do not make
unrelated changes merely to continue the run.

If the requested result cannot satisfy the current run contract, report the
specific blocker rather than silently changing the requested geometry or
output type.

When the complete requested product has `product_validation_status ==
"Passed"` with no blocking failures, call `cad_review` immediately. The review
tool is a read-only quality gate and must be called before completion. If it
fails only with `review_infrastructure` findings, retry `cad_review` without
editing or revalidating the product; infrastructure failures are not CAD
defects. For substantive findings, make a material Python source change, then
call `validate_model` and `cad_review` again. Finish only after `cad_review`
returns `pass`; the host then performs the Accepted promotion.
"""


def _build_agent_system_prompt(
    *,
    workspace_root: str | Path,
    skill_root: str | Path | None,
    run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
) -> str:
    """Add the concrete run-local filesystem boundaries to the Agent prompt."""

    # Physical roots are intentionally omitted from the prompt.  The model
    # sees only the stable virtual routes supplied by CompositeBackend.
    del workspace_root, skill_root
    return (
        _SYSTEM_PROMPT.replace(
            "__CADFLOW_AGENT_RUN_TIMEOUT_SECONDS__",
            _format_timeout_seconds(run_timeout_seconds),
        )
        + """

## Filesystem boundaries for this run

The Agent has exactly two useful virtual routes:

- `/code/` is the Project's Python source workspace. Read and write only
  `/code/**/*.py`; `/code/model.py` is the required stable entry point and
  additional focused helper modules are supported.
- `/skills/` is a read-only Skill reference mount. You may list, search, and read
  relevant Skill files there. You must never create, edit, rename, or delete anything there.

Project logs, metadata, previews, review evidence, and CAD artifacts are not
mounted and must not be searched by guessing host paths. Do not search parent directories, sibling
directories, or other Projects, or any path outside these
virtual routes. File tools are the only workspace mutation interface.
"""
    )


def build_chat_model(settings: AgentSettings) -> Any:
    """Construct the configured LangChain model without exposing its key."""

    from langchain_openai import ChatOpenAI

    arguments: dict[str, Any] = {
        "model": settings.model_id,
        "api_key": settings.api_key,
        "max_retries": MAX_PROVIDER_RETRIES,
        "timeout": settings.run_timeout_seconds,
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
            run_timeout_seconds=settings.run_timeout_seconds,
        ),
        middleware=(
            FilesystemMiddleware(
                backend=backend,
                tools=list(AGENT_FILESYSTEM_TOOLS),
            ),
        ),
        subagents=(),
        skills=["/skills/"] if resolved_skill_root is not None else None,
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
        run_timeout_seconds = self.settings.run_timeout_seconds
        timeout_message = _agent_run_timeout_message(run_timeout_seconds)
        deadline = started + run_timeout_seconds
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
            run_timeout_seconds=run_timeout_seconds,
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
                workspace_root=validator.project_dir,
                skill_root=Path(self.repo_root) / "skills",
            )
            agent_error, timed_out = _invoke_agent_with_deadline(
                agent,
                prompt,
                deadline=deadline,
                cancellation_token=token,
                run_timeout_seconds=run_timeout_seconds,
                conversation_log=self.conversation_log,
                conversation_context=conversation_context,
            )
        except Exception as exc:  # Agent/provider errors become a safe diagnosis.
            agent_error = _safe_failure_reason(exc)

        duration = time.monotonic() - started
        execution = executions[-1] if executions else None
        review_result = review_results[-1] if review_results else None
        if (
            timed_out
            or token.cancellation_reason == "timeout"
            or duration > run_timeout_seconds
        ):
            return AgentRunOutcome(
                validated=False,
                failure_reason=timeout_message,
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
    return result.is_validated_product


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


def _invoke_agent_with_deadline(
    agent: Any,
    prompt: str,
    *,
    deadline: float,
    cancellation_token: CancellationToken,
    run_timeout_seconds: float = DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    conversation_log: ConversationLog | None = None,
    conversation_context: list[dict[str, str]] | None = None,
) -> tuple[str | None, bool]:
    """Invoke the primary Agent and cancel its task on Stop or timeout."""

    timeout_message = _agent_run_timeout_message(run_timeout_seconds)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        cancellation_token.cancel(reason="timeout")
        return timeout_message, True

    async def invoke() -> tuple[str | None, bool]:
        config: dict[str, Any] = {}
        if conversation_log is not None:
            config["callbacks"] = [conversation_log.callback_handler()]
            config["metadata"] = {
                "agent_id": PRIMARY_AGENT_ID,
                "agent_name": PRIMARY_AGENT_NAME,
                "agent_role": PRIMARY_AGENT_ROLE,
            }
            config["tags"] = ["cad-primary"]
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
                        timeout_message
                        if timed_out
                        else "Agent Run stopped by caller",
                        timed_out,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cancellation_token.cancel(reason="timeout")
                    task.cancel()
                    await _await_cancelled_task(task)
                    return timeout_message, True
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
    "AGENT_RUN_TIMEOUT_ENV_VAR",
    "REVIEW_MODEL_ENV_VAR",
    "AGENT_RUN_TIMEOUT_SECONDS",
    "DEFAULT_AGENT_RUN_TIMEOUT_SECONDS",
    "MAX_AGENT_RUN_SECONDS",
    "MAX_PROVIDER_RETRIES",
    "ReferenceGroundedAgent",
    "build_chat_model",
    "build_deep_agent",
    "create_agent_tools",
    "resolve_agent_run_timeout_seconds",
]
