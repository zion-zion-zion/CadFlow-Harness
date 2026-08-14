from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from backend.agent import (
    _build_agent_system_prompt,
    AgentConfigurationError,
    AgentRunError,
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


def test_agent_prompt_routes_through_applicable_skills_without_widening_contract(
    tmp_path: Path,
) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=Path(__file__).parents[1] / "skills",
        example_root=Path(__file__).parents[1] / "examples",
    )

    assert "read applicable CadFlow Skills" in prompt
    assert "Use each Skill's description to decide whether it applies" in prompt
    assert "read the full\nSKILL.md before following it" in prompt
    assert "combine modeling and\nvalidation guidance when both apply" in prompt
    assert "The Project contract below takes precedence for this run" in prompt
    assert "one returned final cad.Shape" in prompt
    assert "read the loaded CadFlow Skill" not in prompt
    assert "locally installed CadFlow or Python API" in prompt
    assert "including compatibility, private, and" in prompt
    assert "non-passing entry-point" in prompt
    assert "Do not use the network or install dependencies" in prompt


def test_agent_prompt_requires_diagnostic_repairs_before_retry(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=None,
        example_root=None,
    )

    assert "On each failed validation:" in prompt
    assert "Identify the failure category from error_type and preflight_status." in prompt
    assert "Identify the likely geometric, API, or source-code cause." in prompt
    assert "Modify only what is necessary to address that specific failure" in prompt
    assert "Revalidate the repaired Model Source only if it has materially changed." in prompt
    assert "Never retry an unchanged or semantically equivalent model" in prompt
    assert "Each retry must address a specific reported failure." in prompt
    assert "There is no CAD\nexecution-count limit." in prompt
    assert "time\nremains within the ten-minute Agent Run deadline" in prompt


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
