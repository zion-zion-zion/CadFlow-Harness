from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent import (
    _build_agent_system_prompt,
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
    execute_tool = next(tool for tool in tools if tool.name == "validate_model")

    with pytest.raises(RuntimeError, match="executor failed"):
        execute_tool.invoke({})
    with pytest.raises(AgentRunError, match="at most 1 CAD execution"):
        execute_tool.invoke({})


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
        example_root=Path(__file__).parents[1] / "examples",
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
        "execute",
    }.issubset(graph_tools)
    # This remains one primary Text-to-CAD agent; general tools do not require
    # delegating the CAD task to another agent.
    assert "task" not in graph_tools


def test_agent_prompt_confines_writes_and_exposes_read_only_references(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).parents[1].resolve()
    project_dir = (tmp_path / "project").resolve()

    prompt = _build_agent_system_prompt(
        workspace_root=project_dir,
        skill_root=repo_root / "skills",
        example_root=repo_root / "examples",
    )

    assert f"The current Project workspace is exactly:\n`{project_dir}`" in prompt
    assert f"The required Model Source is `{project_dir / 'model.py'}`" in prompt
    assert f"- `{repo_root / 'skills'}`" in prompt
    assert f"- `{repo_root / 'examples'}`" in prompt
    assert "read-only reference exceptions" in prompt
    assert "must never create, edit, rename, or delete anything there" in prompt
    assert (
        "Do not search parent\ndirectories, sibling directories, or other Projects"
        in prompt
    )


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
