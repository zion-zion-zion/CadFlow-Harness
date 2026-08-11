from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent import (
    AgentConfigurationError,
    AgentRunError,
    AgentSettings,
    AgentRunService,
    build_chat_model,
    build_deep_agent,
    create_agent_tools,
)
from backend.projects import ProjectState, ProjectStore
from backend.restricted_tools import RestrictedAgentTools


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


def test_agent_tools_are_exactly_the_restricted_issue_01_surface(
    tmp_path: Path,
) -> None:
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()

    tools = create_agent_tools(restricted)

    assert {tool.name for tool in tools} == {
        "read_skill_entry",
        "read_api_index",
        "read_stdlib_index",
        "read_api_doc",
        "read_stdlib_doc",
        "list_examples",
        "read_example",
        "read_model_source",
        "write_model_source",
        "execute_model",
    }


def test_agent_write_tool_requires_reference_grounding(tmp_path: Path) -> None:
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()
    write_tool = next(
        tool
        for tool in create_agent_tools(restricted)
        if tool.name == "write_model_source"
    )

    with pytest.raises(ValueError, match="read the Skill entry"):
        write_tool.invoke({"source": "# guessed\n"})

    restricted.read_skill_entry()
    restricted.read_api_index()
    restricted.read_stdlib_index()
    with pytest.raises(ValueError, match="exact API or stdlib"):
        write_tool.invoke({"source": "# guessed\n"})

    restricted.read_api_doc("model")
    write_tool.invoke({"source": "# grounded\n"})
    assert restricted.read_model_source() == "# grounded\n"


def test_agent_execute_tool_requires_the_current_source_to_be_written(
    tmp_path: Path,
) -> None:
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()
    tools = create_agent_tools(restricted)
    for tool_name, argument in (
        ("read_skill_entry", {}),
        ("read_api_index", {}),
        ("read_stdlib_index", {}),
        ("read_api_doc", {"api_name": "model"}),
    ):
        next(tool for tool in tools if tool.name == tool_name).invoke(argument)

    execute_tool = next(tool for tool in tools if tool.name == "execute_model")

    with pytest.raises(AgentRunError, match="write the complete Model Source"):
        execute_tool.invoke({"api_names": ["model"], "stdlib_names": []})


def test_agent_execution_attempt_is_consumed_when_executor_raises(
    tmp_path: Path,
) -> None:
    class RaisingExecutor:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("executor failed")

    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
        executor=RaisingExecutor(),
    )
    restricted.begin_run()
    tools = create_agent_tools(restricted)
    for tool_name, argument in (
        ("read_skill_entry", {}),
        ("read_api_index", {}),
        ("read_stdlib_index", {}),
        ("read_api_doc", {"api_name": "model"}),
    ):
        next(tool for tool in tools if tool.name == tool_name).invoke(argument)
    next(tool for tool in tools if tool.name == "write_model_source").invoke(
        {"source": "# grounded\n"}
    )
    execute_tool = next(tool for tool in tools if tool.name == "execute_model")
    arguments = {"api_names": ["model"], "stdlib_names": []}

    with pytest.raises(RuntimeError, match="executor failed"):
        execute_tool.invoke(arguments)
    with pytest.raises(AgentRunError, match="at most 1 CAD execution"):
        execute_tool.invoke(arguments)


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

    agent = build_deep_agent(settings, create_agent_tools(restricted), model=model)

    assert agent.get_graph().nodes
    graph_tools = set(agent.get_graph().nodes["tools"].data.tools_by_name)
    assert {
        "read_skill_entry",
        "read_api_index",
        "read_stdlib_index",
        "read_api_doc",
        "read_stdlib_doc",
        "list_examples",
        "read_example",
        "read_model_source",
        "write_model_source",
        "execute_model",
    }.issubset(graph_tools)
    # Deep Agents keeps its required middleware/tool node in the compiled
    # graph, but HarnessProfile filters these names before the model sees them.
    # The task tool is absent entirely because the default subagent is disabled.
    assert "task" not in graph_tools


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
