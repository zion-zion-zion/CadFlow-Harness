"""Deep Agents runtime construction and bounded primary-agent execution."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from langchain_core.tools import BaseTool

from ..agent_logging import (
    PRIMARY_AGENT_ID,
    PRIMARY_AGENT_NAME,
    PRIMARY_AGENT_ROLE,
    ConversationLog,
)
from ..agent_backend import create_agent_backend
from ..cad_execution_contract import ExecutionResult
from ..cad_process import CAD_EXECUTION_TIMEOUT_SECONDS, CancellationToken
from ..cad_review import ReviewResult
from ..contracts import ToolUseRecord
from ..events import ProgressUpdate
from ..restricted_tools import AgentModelValidator
from .outcome import AgentRunOutcome, is_validated_result
from .prompt import _build_agent_system_prompt
from .settings import (
    AGENT_EXCLUDED_TOOLS,
    AGENT_FILESYSTEM_TOOLS,
    AgentSettings,
    DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    _agent_run_timeout_message,
    build_chat_model,
)
from .tools import (
    _safe_failure_reason,
    _safe_failure_text,
    create_agent_tools,
)


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
            from .outcome import AgentRunError

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
            agent = _build_agent_for_run(
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


def _build_agent_for_run(settings: AgentSettings, tools: Any, **kwargs: Any) -> Any:
    """Resolve the package export so legacy monkeypatches remain effective."""

    package = sys.modules.get("backend.agent")
    package_builder = getattr(package, "build_deep_agent", None)
    if package_builder is not None and package_builder is not build_deep_agent:
        return package_builder(settings, tools, **kwargs)
    return build_deep_agent(settings, tools, **kwargs)


def _is_validated_result(result: ExecutionResult) -> bool:
    return is_validated_result(result)


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


__all__ = [
    "ReferenceGroundedAgent",
    "build_deep_agent",
    "_build_agent_system_prompt",
    "_is_validated_result",
    "_invoke_agent_with_deadline",
]
