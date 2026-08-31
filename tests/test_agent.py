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
    DEFAULT_AGENT_RUN_TIMEOUT_SECONDS,
    MAX_AGENT_RUN_SECONDS,
    ReferenceGroundedAgent,
    _is_validated_result,
    _invoke_agent_with_deadline,
    build_chat_model,
    build_deep_agent,
    create_agent_tools,
    resolve_agent_run_timeout_seconds,
)
from backend.projects import ProjectState, ProjectStore
from backend.cad_executor import CancellationToken, ExecutionResult
from backend.restricted_tools import RestrictedAgentTools
from backend.scene_validation import SceneParseResult


def test_agent_run_timeout_defaults_to_twenty_minutes() -> None:
    assert DEFAULT_AGENT_RUN_TIMEOUT_SECONDS == 1200.0
    assert MAX_AGENT_RUN_SECONDS == DEFAULT_AGENT_RUN_TIMEOUT_SECONDS
    assert resolve_agent_run_timeout_seconds({}) == 1200.0


def test_agent_run_timeout_is_loaded_from_environment() -> None:
    settings = AgentSettings.from_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_MODEL_ID": "cad-model",
            "CADFLOW_AGENT_RUN_TIMEOUT_SECONDS": "37.5",
        }
    )

    assert settings.run_timeout_seconds == 37.5


@pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf"])
def test_agent_run_timeout_rejects_invalid_environment_values(value: str) -> None:
    with pytest.raises(
        AgentConfigurationError,
        match="CADFLOW_AGENT_RUN_TIMEOUT_SECONDS",
    ):
        resolve_agent_run_timeout_seconds(
            {"CADFLOW_AGENT_RUN_TIMEOUT_SECONDS": value}
        )


def test_agent_accepts_a_deterministically_passed_assembly_candidate() -> None:
    result = ExecutionResult(
        status="succeeded",
        exit_code=0,
        error=None,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        final_shape_count=1,
        solid_count=2,
        solid_volume=16.0,
        scene_artifact_exists=True,
        scene_parse_result=SceneParseResult(valid=True),
        artifact_entries=(
            "assumptions.json",
            "bom.json",
            "model.scene.zip",
            "model.semantic.json",
            "model.step",
            "parts/housing.step",
            "parts/shaft.step",
            "product.json",
            "source.zip",
            "validation.json",
        ),
        duration_seconds=1.0,
        result_kind="assembly",
        component_count=2,
        leaf_part_count=2,
        unique_part_count=2,
        product_manifest_path="artifacts/product.json",
        product_status="Draft",
        product_validation_status="Passed",
        product_validation_failures=(),
    )

    assert _is_validated_result(result) is True


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


def test_reference_agent_uses_configured_run_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingAgent:
        async def ainvoke(self, *_args: object, **_kwargs: object) -> None:
            await asyncio.Event().wait()

    monkeypatch.setattr(
        "backend.agent.build_deep_agent",
        lambda *_args, **_kwargs: BlockingAgent(),
    )
    agent = ReferenceGroundedAgent(
        settings=AgentSettings(
            model_id="cad-model",
            api_key="unit-test-key",
            run_timeout_seconds=0.01,
        ),
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
    )

    outcome = agent.run("Create a box.")

    assert outcome.validated is False
    assert outcome.failure_reason == (
        "Agent Run exceeded the configured 0.01-second wall-clock limit"
    )
    assert outcome.duration_seconds is not None
    assert outcome.duration_seconds < 1.0


def test_agent_settings_are_backend_only_and_require_model_credentials() -> None:
    settings = AgentSettings.from_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_MODEL_ID": "cad-model",
            "OPENAI_BASE_URL": "https://provider.invalid/v1",
            "OPENAI_REASONING_EFFORT": "high",
            "OPENAI_REASONING_SUMMARY": "auto",
        }
    )

    assert settings.provider == "openai"
    assert settings.model_id == "cad-model"
    assert settings.review_model_id is None
    assert settings.base_url == "https://provider.invalid/v1"
    assert settings.reasoning_effort == "high"
    assert settings.reasoning_summary == "auto"
    assert "secret-key" not in repr(settings)

    default_settings = AgentSettings.from_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_MODEL_ID": "cad-model",
        }
    )
    assert default_settings.reasoning_effort is None

    review_settings = AgentSettings.from_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_MODEL_ID": "cad-model",
            "OPENAI_REVIEW_MODEL_ID": "review-model",
        }
    )
    assert review_settings.review_model_id == "review-model"

    with pytest.raises(AgentConfigurationError, match="OPENAI_API_KEY"):
        AgentSettings.from_environment({"OPENAI_MODEL_ID": "cad-model"})

    chat_settings = AgentSettings.from_environment(
        {
            "OPENAI_API_KEY": "secret-key",
            "OPENAI_MODEL_ID": "cad-model",
            "OPENAI_REASONING_EFFORT": "none",
        }
    )
    assert chat_settings.reasoning_effort == "none"

    with pytest.raises(AgentConfigurationError, match="OPENAI_REASONING_SUMMARY"):
        AgentSettings.from_environment(
            {
                "OPENAI_API_KEY": "secret-key",
                "OPENAI_MODEL_ID": "cad-model",
                "OPENAI_REASONING_SUMMARY": "verbose",
            }
        )

    with pytest.raises(AgentConfigurationError, match="must be one of"):
        AgentSettings.from_environment(
            {
                "OPENAI_API_KEY": "secret-key",
                "OPENAI_MODEL_ID": "cad-model",
                "OPENAI_REASONING_EFFORT": "xhigh",
            }
        )


def test_agent_tools_include_the_cad_specific_surface(
    tmp_path: Path,
) -> None:
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()

    tools = create_agent_tools(restricted)

    assert {tool.name for tool in tools} == {"validate_model", "cad_review"}
    validate_tool = next(tool for tool in tools if tool.name == "validate_model")
    assert "bounded, structured failure evidence" in validate_tool.description
    review_tool = next(tool for tool in tools if tool.name == "cad_review")
    review_description = " ".join(review_tool.description.split())
    assert "complete requested geometry" in review_description
    assert "Do not call it for successful intermediate checkpoints" in (
        review_description
    )


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
        run_timeout_seconds=37.5,
    )
    restricted = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1], project_dir=tmp_path
    )
    restricted.begin_run()
    model = build_chat_model(settings)

    assert model.request_timeout == 37.5

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
        "delete",
    }.issubset(graph_tools)
    assert {"glob", "grep"}.isdisjoint(graph_tools)
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
        "delete",
        "write_todos",
        "validate_model",
        "cad_review",
    }
    assert {"execute", "glob", "grep", "task"}.isdisjoint(
        model.bound_tool_names
    )
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

    assert "The Agent has exactly two useful virtual routes" in prompt
    assert "`/code/model.py`" in prompt
    assert "`/skills/`" in prompt
    assert str(project_dir) not in prompt
    assert str(repo_root / "skills") not in prompt
    assert "read-only Skill reference" in prompt
    assert "examples" not in prompt
    assert "must never" in prompt
    assert "create, edit, rename, or delete anything there" in prompt
    assert "Read and write only\n  `/code/**/*.py`" in prompt
    assert "Do not search parent directories, sibling" in prompt
    assert "directories, or other Projects" in prompt


def test_agent_prompt_routes_shape_and_assembly_through_applicable_skills(
    tmp_path: Path,
) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=Path(__file__).parents[1] / "skills",
    )

    assert "`build_model(model: cad.Model) -> cad.Shape | cad.Assembly`" in prompt
    assert "Return a `cad.Shape` only when" in prompt
    assert "Return a semantic `cad.Assembly`" in prompt
    assert "Never fuse multiple parts" in prompt
    assert "strict constraint solve and every residual" in prompt
    assert "collision" not in prompt.lower()
    assert "Passed Draft to Accepted" in prompt
    assert "Use `product_validation_checks` for solve diagnosis" in prompt
    assert "`validation_short_circuited=true`" in prompt
    assert "absent downstream\nartifacts as another source defect" in prompt
    assert "do not leave temporary solve, inspection, or debug-print probes" in prompt
    assert "Read any relevant CadFlow Skills" in prompt
    assert "You may choose more than one Skill" in prompt
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
    assert "Inspect `product_validation_checks`" in prompt
    assert "residual IDs, or envelope measurements" in prompt
    assert "Call `validate_model` again only after the source has materially changed." in prompt
    assert "Never retry an unchanged or semantically equivalent Model Source." in prompt
    assert "unrelated changes merely to continue the run." in prompt
    assert "complete requested product has `product_validation_status ==" in prompt
    assert '"Passed"` with no blocking failures' in prompt
    assert "`cad_review` immediately" in prompt
    assert "fails only with `review_infrastructure` findings" in prompt
    assert "retry `cad_review` without\nediting or revalidating" in prompt
    assert "infrastructure failures are not CAD\ndefects" in prompt


def test_agent_prompt_stages_complex_part_and_assembly_work(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=None,
    )

    assert "For simple work, implement the complete Model Source" in prompt
    assert "For complex single-part work, use staged implementation" in prompt
    assert "For complex Assembly work, stage shared dimensions" in prompt
    assert "Use judgment rather than a fixed feature-count threshold." in prompt
    assert "normally plan two to four materially distinct validation" in prompt
    assert "A single-part stage returns one meaningful" in prompt
    assert "An Assembly stage returns a coherent partial" in prompt
    assert "Preserve a requested single part as a Shape" in prompt
    assert "retain working\nbehavior and add requested feature" in prompt
    assert "Call `validate_model` after each planned stage." in prompt
    assert "continue to the next planned stage without\ncalling `cad_review`" in prompt
    assert "Never stack more features on a failed stage." in prompt
    assert "passing candidate is intermediate only when at least one explicit" in prompt
    assert "Leave enough of the reported run budget" in prompt


def test_agent_prompt_defines_multi_file_product_contract(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=Path(__file__).parents[1] / "skills",
        run_timeout_seconds=37.5,
    )

    assert "split `/code/` into focused Python modules" in prompt
    assert "`model.py` as the small orchestration entry point" in prompt
    assert '"envelope": {"max_size_mm":' in prompt
    assert '"collision_exclusions"' not in prompt
    assert "A successful subprocess can still be a Draft" in prompt
    assert "configured wall-clock budget of\n37.5 seconds" in prompt
    assert "do not wait for\nhuman approval" in prompt


def test_agent_prompt_requires_todo_plan_before_source_edits(tmp_path: Path) -> None:
    prompt = _build_agent_system_prompt(
        workspace_root=tmp_path,
        skill_root=None,
    )

    assert "Before making any change to `/code/model.py`" in prompt
    assert "you\nmust call `write_todos`" in prompt
    assert "required even when the request appears simple" in prompt
    assert "Do not write or edit Project\nsource before the initial todo plan exists." in prompt
    assert "Keep the plan current as the\nwork progresses" in prompt


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
    store.submit_prompt(project.project_id, "Create a box.")
    conversation_log = store.conversation_log(
        project.project_id,
        turn_id="turn-1",
        request_id="request-1",
        user_message="Create a box.",
    )

    outcome = service.run(
        project.project_id,
        "Create a box.",
        cancellation_token=CancellationToken(),
        progress_callback=lambda _update: None,
        conversation_log=conversation_log,
    )

    assert outcome.validated is False
    assert store.get_project(project.project_id).state is ProjectState.RUNNING
    assert outcome.failure_reason == "OPENAI_API_KEY is required"


def test_agent_run_service_persists_provider_token_usage(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)

    class UsageReportingAgent:
        def __init__(self, *, conversation_log, **_kwargs: object) -> None:
            self.conversation_log = conversation_log

        def run(self, _prompt: str, **_kwargs: object) -> AgentRunOutcome:
            self.conversation_log.callback_handler().on_llm_end(
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
    store.submit_prompt(project.project_id, "Create a box.")
    conversation_log = store.conversation_log(
        project.project_id,
        turn_id="turn-1",
        request_id="request-1",
        user_message="Create a box.",
    )

    outcome = service.run(
        project.project_id,
        "Create a box.",
        cancellation_token=CancellationToken(),
        progress_callback=lambda _update: None,
        conversation_log=conversation_log,
    )

    assert outcome.token_usage == {
        "total_tokens": 100,
        "input_tokens": 80,
        "cached_input_tokens": 50,
        "uncached_input_tokens": 30,
        "output_tokens": 20,
    }
    assert store.get_project(project.project_id).state is ProjectState.RUNNING
    assert store.read_diagnostics(project.project_id) is None
    assert conversation_log.turn("turn-1")["status"] == "running"  # type: ignore[index]
