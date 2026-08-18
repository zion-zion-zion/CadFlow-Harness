from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import ClassVar

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
import pytest

from backend.agent import (
    _build_agent_system_prompt,
    AgentConfigurationError,
    AgentRunError,
    AgentRunOutcome,
    AgentSettings,
    AgentRunService,
    MAX_AGENT_RUN_SECONDS,
    _invoke_agent_with_deadline,
    build_chat_model,
    build_deep_agent,
    create_agent_tools,
)
from backend.projects import ProjectState, ProjectStore
from backend.cad_executor import CancellationToken
from backend.restricted_tools import RestrictedAgentTools


def test_agent_run_timeout_is_ten_minutes() -> None:
    assert MAX_AGENT_RUN_SECONDS == 600.0


def test_agent_invocation_is_cancelled_before_stop_returns() -> None:
    class BlockingAgent:
        def __init__(self) -> None:
            self.sync_started = threading.Event()
            self.sync_release = threading.Event()
            self.async_started = threading.Event()
            self.async_cancelled = threading.Event()

        def invoke(self, *_args: object, **_kwargs: object) -> None:
            self.sync_started.set()
            self.sync_release.wait(2.0)

        async def ainvoke(self, *_args: object, **_kwargs: object) -> None:
            self.async_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.async_cancelled.set()
                raise

    agent = BlockingAgent()
    token = CancellationToken()
    result: list[tuple[str | None, bool]] = []
    worker = threading.Thread(
        target=lambda: result.append(
            _invoke_agent_with_deadline(
                agent,
                "Create a part.",
                deadline=time.monotonic() + 5.0,
                cancellation_token=token,
            )
        )
    )
    worker.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if agent.sync_started.is_set() or agent.async_started.is_set():
            break
        time.sleep(0.005)

    try:
        token.cancel()
        worker.join(1.0)

        assert worker.is_alive() is False
        assert result == [("Agent Run stopped by caller", False)]
        assert agent.async_cancelled.is_set()
        assert agent.sync_started.is_set() is False
    finally:
        agent.sync_release.set()
        worker.join(1.0)


def test_agent_settings_are_backend_only_and_require_model_credentials() -> None:
    settings = AgentSettings.from_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_MODEL_ID": "cad-model",
            "OPENAI_BASE_URL": "https://provider.invalid/v1",
        }
    )

    assert settings.provider == "openai"
    assert settings.model_id == "cad-model"
    assert settings.base_url == "https://provider.invalid/v1"
    assert "secret-key" not in repr(settings)

    with pytest.raises(AgentConfigurationError, match="OPENAI_API_KEY"):
        AgentSettings.from_environment({"OPENAI_MODEL_ID": "cad-model"})


def test_agent_tools_include_the_cad_specific_surface(
    tmp_path: Path,
) -> None:
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()

    tools = create_agent_tools(restricted)

    assert {tool.name for tool in tools} == {"validate_model"}


def test_validator_has_no_reference_or_source_write_gate(tmp_path: Path) -> None:
    class RecordingExecutor:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("dedicated executor reached")

    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
        executor=RecordingExecutor(),
    )
    restricted.begin_run()
    tools = {tool.name: tool for tool in create_agent_tools(restricted)}

    with pytest.raises(RuntimeError, match="dedicated executor reached"):
        tools["validate_model"].invoke({})


def test_agent_execution_can_retry_after_executor_raises(
    tmp_path: Path,
) -> None:
    class RaisingExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, *_args: object, **_kwargs: object) -> object:
            self.calls += 1
            raise RuntimeError("executor failed")

    executor = RaisingExecutor()
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
        executor=executor,
    )
    restricted.begin_run()
    tools = create_agent_tools(restricted)
    execute_tool = next(tool for tool in tools if tool.name == "validate_model")

    with pytest.raises(RuntimeError, match="executor failed"):
        execute_tool.invoke({})
    with pytest.raises(RuntimeError, match="executor failed"):
        execute_tool.invoke({})
    assert executor.calls == 2


def test_deep_agent_can_be_compiled_without_network_call(tmp_path: Path) -> None:
    settings = AgentSettings(
        model_id="cad-model",
        api_key="unit-test-key",
        base_url="https://provider.invalid/v1",
    )
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()
    model = build_chat_model(settings)

    agent = build_deep_agent(
        settings,
        create_agent_tools(restricted),
        model=model,
        workspace_root=tmp_path,
        skill_root=Path(__file__).parents[1] / "skills",
    )

    assert agent.get_graph().nodes
    graph_tools = set(agent.get_graph().nodes["tools"].data.tools_by_name)
    assert "validate_model" in graph_tools
    assert {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
    }.issubset(graph_tools)
    # This remains one primary Text-to-CAD agent; general tools do not require
    # delegating the CAD task to another agent.
    assert "task" not in graph_tools


def test_deep_agent_model_sees_only_the_confirmed_tool_surface(
    tmp_path: Path,
) -> None:
    class ToolRecordingModel(GenericFakeChatModel):
        bound_tool_names: ClassVar[list[str]] = []
        bound_tool_descriptions: ClassVar[dict[str, str]] = {}
        model_request_text: ClassVar[str] = ""

        def _generate(
            self, messages: list[object], **kwargs: object
        ) -> object:
            type(self).model_request_text = "\n".join(
                str(message.content) for message in messages  # type: ignore[attr-defined]
            )
            return super()._generate(messages, **kwargs)  # type: ignore[arg-type,return-value]

        def bind_tools(
            self, tools: object, **_kwargs: object
        ) -> "ToolRecordingModel":
            type(self).bound_tool_names = [
                item.name for item in tools  # type: ignore[union-attr]
            ]
            type(self).bound_tool_descriptions = {
                item.name: item.description  # type: ignore[union-attr]
                for item in tools  # type: ignore[union-attr]
            }
            return self

    model = ToolRecordingModel(messages=iter(["done"]))
    settings = AgentSettings(
        model_id="cad-model",
        api_key="unit-test-key",
        provider="toolrecordingmodel",
    )
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()

    agent = build_deep_agent(
        settings,
        create_agent_tools(restricted),
        model=model,
        workspace_root=tmp_path,
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "Inspect the workspace."}]}
    )

    assert set(model.bound_tool_names) == {
        "read_file",
        "write_file",
        "edit_file",
        "ls",
        "glob",
        "grep",
        "write_todos",
        "validate_model",
    }
    assert {"execute", "delete", "task"}.isdisjoint(model.bound_tool_names)
    assert "execute" not in model.bound_tool_descriptions["grep"].lower()
    assert "Shell paths vs. virtual paths" not in model.model_request_text
    assert "Host path mappings" not in model.model_request_text
    assert "execute tool runs commands" not in model.model_request_text


def test_agent_prompt_confines_writes_and_exposes_read_only_references(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).parents[1].resolve()
    project_dir = (tmp_path / "project").resolve()

    prompt = _build_agent_system_prompt(
        workspace_root=project_dir,
        skill_root=repo_root / "skills",
    )

    assert f"The current Project workspace is exactly:\n`{project_dir}`" in prompt
    assert f"The required Model Source is `{project_dir / 'model.py'}`" in prompt
    assert f"- `{repo_root / 'skills'}`" in prompt
    assert "read-only Skill reference" in prompt
    assert "examples" not in prompt
    assert "must never" in prompt
    assert "create, edit, rename, or delete anything there" in prompt
    assert "create or edit only model.py and local\nPython helper modules" in prompt
    assert "Do not search parent directories, sibling" in prompt
    assert "directories, or other Projects" in prompt


def test_agent_prompt_routes_through_applicable_skills_without_widening_contract(
    tmp_path: Path,
) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=Path(__file__).parents[1] / "skills",
    )

    assert "The current executor accepts one final `cad.Shape`" in prompt
    assert "not a general limitation of CadFlow" in prompt
    assert "Select the Skills whose descriptions match the request." in prompt
    assert "Read each selected" in prompt
    assert "If Skills disagree, preserve the" in prompt
    assert "current run contract" in prompt
    assert "Use only public CadFlow and Python APIs" in prompt
    assert "Do not import private CadFlow engine" in prompt
    assert "It may be empty or may contain an" in prompt
    assert "non-passing entry-point" not in prompt
    assert "including compatibility, private, and" not in prompt
    assert "There is no shell tool in this run" not in prompt
    assert "do not execute commands" not in prompt
    assert "Do not use the network or install dependencies" not in prompt
    assert "trusted local development environment" not in prompt


def test_agent_prompt_requires_diagnostic_repairs_before_retry(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=None,
    )

    assert "When validation fails:" in prompt
    assert "Identify the reported failure and its likely cause." in prompt
    assert "Make a concrete, material source change" in prompt
    assert "Call `validate_model` again only after the source has materially changed." in prompt
    assert "Never retry an unchanged or semantically equivalent Model Source." in prompt
    assert "unrelated changes merely to continue the run." in prompt
    assert "When validation succeeds, stop all further tool calls immediately." in prompt


def test_agent_prompt_requires_todo_plan_before_source_edits(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=None,
    )

    assert "Before making any change to `model.py`" in prompt
    assert "required even when the request appears simple" in prompt
    assert "Do not write or edit Project source before the initial\ntodo plan exists." in prompt
    assert "Immediately after the SPEC, call `write_todos`" in prompt
    assert "Keep the todo list current as work moves\nforward." in prompt


def test_agent_prompt_defines_request_spec_planning_sequence(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=Path(__file__).parents[1] / "skills",
    )

    assert "## Planning phase" in prompt
    assert "two separate planning artifacts" in prompt
    assert "Inspect the current `model.py` and the available Skill metadata." in prompt
    assert "Read each selected\n   full `SKILL.md`" in prompt
    assert "Skill metadata is an index, not a substitute for the full instructions." in prompt
    assert "SPEC\n   Intent:" in prompt
    assert "Hard requirements:" in prompt
    assert "Constraints:" in prompt
    assert "Assumptions:" in prompt
    assert "Skill guidance:" in prompt
    assert "Do not add an acceptance-criteria section" in prompt
    assert "Immediately after the SPEC, call `write_todos` with 3-6 high-level execution" in prompt
    assert "Todo items track actions and status only; do not copy the full SPEC into" in prompt
    assert "emit a normal assistant text block beginning with `SPEC UPDATE`" in prompt
    assert "the current run contract" in prompt
    assert "Do not rewrite the SPEC for an ordinary implementation or API error." in prompt

    discovery = prompt.index("Read each selected")
    spec = prompt.index("SPEC\n   Intent:")
    todos = prompt.index("Immediately after the SPEC")
    source_gate = prompt.index("Do not write or edit Project source before the initial")
    assert discovery < spec < todos < source_gate


def test_missing_configuration_fails_a_project_without_calling_a_model(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)

    def missing_settings() -> AgentSettings:
        raise AgentConfigurationError("OPENAI_API_KEY is required")

    service = AgentRunService(
        store=store,
        repo_root=Path(__file__).parents[1],
        settings_factory=missing_settings,
    )
    project = store.create_project("No credentials")

    outcome = service.run(project.project_id, "Create a box.")

    assert outcome.validated is False
    assert store.get_project(project.project_id).state is ProjectState.FAILED
    assert outcome.failure_reason == "OPENAI_API_KEY is required"


def test_agent_run_service_persists_provider_token_usage(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)

    class UsageReportingAgent:
        def __init__(self, *, run_log, **_kwargs: object) -> None:
            self.run_log = run_log

        def run(self, _prompt: str, **_kwargs: object) -> AgentRunOutcome:
            self.run_log.callback_handler().on_llm_end(
                {
                    "generations": [
                        [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "done",
                                    "usage_metadata": {
                                        "input_tokens": 80,
                                        "output_tokens": 20,
                                        "total_tokens": 100,
                                        "input_token_details": {
                                            "cache_read": 50,
                                        },
                                    },
                                }
                            }
                        ]
                    ]
                },
                run_id="model-1",
            )
            return AgentRunOutcome(
                validated=False,
                failure_reason="expected failure",
                duration_seconds=1.25,
            )

    service = AgentRunService(
        store=store,
        repo_root=Path(__file__).parents[1],
        settings_factory=lambda: AgentSettings(
            model_id="cad-model", api_key="test-key"
        ),
        agent_factory=UsageReportingAgent,
    )
    project = store.create_project("Measured run")

    outcome = service.run(project.project_id, "Create a box.")

    assert outcome.token_usage == {
        "total_tokens": 100,
        "input_tokens": 80,
        "cached_input_tokens": 50,
        "uncached_input_tokens": 30,
        "output_tokens": 20,
    }
    diagnostics = store.read_diagnostics(project.project_id)
    assert diagnostics is not None
    assert diagnostics["duration_seconds"] == 1.25
    assert diagnostics["token_usage"] == outcome.token_usage
