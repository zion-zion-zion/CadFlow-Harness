"""Reference-grounded Deep Agent orchestration for one CAD generation run."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from langchain_core.tools import BaseTool, tool

from .cad_executor import ExecutionResult, redact_credentials
from .contracts import ToolUseRecord
from .projects import ProjectStore
from .restricted_tools import RestrictedAgentTools


class AgentConfigurationError(RuntimeError):
    """Raised when the backend model configuration is incomplete."""


class AgentRunError(RuntimeError):
    """Raised when a run cannot produce a structured Agent outcome."""


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
    """Safe outcome of one reference-grounded generation attempt."""

    validated: bool
    failure_reason: str | None = None
    execution_result: ExecutionResult | None = None
    tool_use_records: tuple[ToolUseRecord, ...] = ()

    @property
    def status(self) -> str:
        return "succeeded" if self.validated else "failed"

    def diagnostics(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failure_reason": self.failure_reason,
            "execution_result": (
                self.execution_result.to_dict()
                if self.execution_result is not None
                else None
            ),
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

After writing the complete Model Source, call execute_model exactly once for
this issue's single successful-generation path. Pass the exact API and stdlib
names used by the source so the tool can verify the reference reads. Treat its
structured result as the source of truth; do not claim success from prose.
"""


def build_chat_model(settings: AgentSettings) -> Any:
    """Construct the configured LangChain model without exposing its key."""

    from langchain_openai import ChatOpenAI

    arguments: dict[str, Any] = {
        "model": settings.model_id,
        "api_key": settings.api_key,
    }
    if settings.base_url is not None:
        arguments["base_url"] = settings.base_url
    return ChatOpenAI(**arguments)


def create_agent_tools(
    restricted: RestrictedAgentTools,
    *,
    max_executions: int = 1,
    on_execution: Callable[[ExecutionResult], None] | None = None,
) -> tuple[BaseTool, ...]:
    """Adapt issue 01's narrow methods to LangChain tools.

    The wrappers close over one ``RestrictedAgentTools`` instance, so each
    Agent Run receives fresh reference gates and no Project-local state leaks
    into another run.
    """

    execution_attempts = 0
    source_written = False

    @tool("read_skill_entry")
    def read_skill_entry() -> str:
        """Read the packaged SimpleCADAPI Skill entry document."""

        return restricted.read_skill_entry()

    @tool("read_api_index")
    def read_api_index() -> str:
        """Read the required SimpleCADAPI API index."""

        return restricted.read_api_index()

    @tool("read_stdlib_index")
    def read_stdlib_index() -> str:
        """Read the required Python stdlib reference index."""

        return restricted.read_stdlib_index()

    @tool("read_api_doc")
    def read_api_doc(api_name: str) -> str:
        """Read one exact SimpleCADAPI API document by name."""

        return restricted.read_api_doc(api_name)

    @tool("read_stdlib_doc")
    def read_stdlib_doc(stdlib_name: str) -> str:
        """Read one exact Python stdlib document by name."""

        return restricted.read_stdlib_doc(stdlib_name)

    @tool("list_examples")
    def list_examples() -> list[str]:
        """List the packaged repository examples."""

        return list(restricted.list_examples())

    @tool("read_example")
    def read_example(relative_path: str) -> str:
        """Read one relevant packaged repository example."""

        return restricted.read_example(relative_path)

    @tool("read_model_source")
    def read_model_source() -> str:
        """Read the complete current Project Model Source."""

        return restricted.read_model_source()

    @tool("write_model_source")
    def write_model_source(source: str) -> str:
        """Replace the complete current Project Model Source with source text."""

        nonlocal source_written
        restricted.require_reference_grounding_for_write()
        restricted.write_model_source(source)
        source_written = True
        return "Model Source written for the current Project."

    @tool("execute_model")
    def execute_model(api_names: list[str], stdlib_names: list[str]) -> dict[str, Any]:
        """Execute the current Model Source after exact reference reads."""

        nonlocal execution_attempts
        if not source_written:
            raise AgentRunError("write the complete Model Source before execute_model")
        if execution_attempts >= max_executions:
            raise AgentRunError(
                f"this Agent Run allows at most {max_executions} CAD execution"
            )
        execution_attempts += 1
        result = restricted.execute_model(
            api_names=api_names,
            stdlib_names=stdlib_names,
        )
        if not isinstance(result, ExecutionResult):
            raise AgentRunError("execute_model returned an invalid structured result")
        if on_execution is not None:
            on_execution(result)
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
    """Run one primary Deep Agent against one Project Model Source."""

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

    def run(self, prompt: str) -> AgentRunOutcome:
        if not isinstance(prompt, str) or not prompt.strip():
            raise AgentRunError("Prompt must not be empty")
        restricted = RestrictedAgentTools(
            repo_root=self.repo_root,
            project_dir=self.project_dir,
            executor=self.executor,
        )
        scaffold = restricted.begin_run()
        initial_source = scaffold.model_path.read_text(encoding="utf-8")
        executions: list[ExecutionResult] = []
        agent_tools = create_agent_tools(
            restricted,
            max_executions=1,
            on_execution=executions.append,
        )
        agent_error: str | None = None
        try:
            agent = build_deep_agent(
                self.settings,
                agent_tools,
                model=self.model,
            )
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
        except Exception as exc:  # Agent/provider errors become a safe diagnosis.
            agent_error = _safe_failure_reason(exc)

        execution = executions[-1] if executions else None
        if execution is None:
            return AgentRunOutcome(
                validated=False,
                failure_reason=agent_error
                or "Agent finished without executing the Model Source",
                tool_use_records=restricted.tool_use_records,
            )
        if not _is_validated_result(execution):
            return AgentRunOutcome(
                validated=False,
                failure_reason=execution.error
                or agent_error
                or "CAD execution did not produce a Validated Result",
                execution_result=execution,
                tool_use_records=restricted.tool_use_records,
            )
        if not _source_was_written(restricted, initial_source):
            return AgentRunOutcome(
                validated=False,
                failure_reason="Agent did not write a complete Model Source",
                execution_result=execution,
                tool_use_records=restricted.tool_use_records,
            )
        return AgentRunOutcome(
            validated=True,
            execution_result=execution,
            tool_use_records=restricted.tool_use_records,
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

    def run(self, project_id: str, prompt: str) -> AgentRunOutcome:
        project = self.store.submit_prompt(project_id, prompt)
        try:
            settings = self.settings_factory()
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
            self.store.mark_failed(project.project_id, reason, outcome.diagnostics())
            return outcome

        try:
            agent = self.agent_factory(
                settings=settings,
                repo_root=self.repo_root,
                project_dir=self.store.project_directory(project.project_id),
            )
            outcome = agent.run(project.prompt or prompt)
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            outcome = AgentRunOutcome(validated=False, failure_reason=reason)
        if outcome.validated:
            self.store.mark_succeeded(project.project_id, outcome.diagnostics())
        else:
            self.store.mark_failed(
                project.project_id,
                outcome.failure_reason or "Agent Run failed",
                outcome.diagnostics(),
            )
        return outcome


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


def _source_was_written(restricted: RestrictedAgentTools, initial_source: str) -> bool:
    records = restricted.tool_use_records
    if not any(record.tool_name == "write_model_source" for record in records):
        return False
    return restricted.read_model_source() != initial_source


def _safe_failure_reason(error: Exception) -> str:
    message = (
        str(error).strip().splitlines()[0]
        if str(error).strip()
        else type(error).__name__
    )
    return redact_credentials(message)[:500]


__all__ = [
    "AgentConfigurationError",
    "AgentRunError",
    "AgentRunOutcome",
    "AgentRunService",
    "AgentSettings",
    "ReferenceGroundedAgent",
    "RESTRICTED_BUILTIN_TOOL_NAMES",
    "build_chat_model",
    "build_deep_agent",
    "create_agent_tools",
]
