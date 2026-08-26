from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from backend.agent_logging import ConversationLog
from backend.cad_executor import CADExecutor, ExecutionResult
from backend.cad_review import _default_reviewer_factory, _review_prompt, review_cad
from backend.agent import create_agent_tools
from backend.model_source import create_model_source
from backend.restricted_tools import AgentModelValidator
from backend.scene_validation import SceneParseResult


class _FakeReviewer:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def invoke(self, _messages: object, config: object = None) -> object:
        del config
        return self.payload


def test_default_reviewer_uses_bounded_low_effort_responses_reasoning() -> None:
    class _Settings:
        model_id = "cad-model"
        review_model_id = "review-model"
        api_key = "test-key"
        base_url = "https://provider.invalid/v1"
        reasoning_effort = "high"
        reasoning_summary = "detailed"
        use_responses_api = True

    reviewer = _default_reviewer_factory(_Settings())

    assert reviewer.model_name == "review-model"
    assert reviewer.reasoning_effort is None
    assert reviewer.reasoning == {"effort": "low"}
    assert reviewer.request_timeout == 90
    assert reviewer.use_responses_api is True

    class _LegacySettings:
        model_id = "cad-model"
        api_key = "test-key"
        base_url = "https://provider.invalid/v1"
        use_responses_api = True

    fallback_reviewer = _default_reviewer_factory(_LegacySettings())
    assert fallback_reviewer.model_name == "cad-model"


def _build_project(tmp_path: Path) -> ExecutionResult:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        "import cadflow as cad\n\n"
        "def build_model(model: cad.Model):\n"
        "    return model.box(width=10.0, depth=20.0, height=30.0)\n",
        encoding="utf-8",
    )
    return CADExecutor().execute(tmp_path, timeout_seconds=30.0)


def test_validate_model_generates_hash_bound_review_evidence(tmp_path: Path) -> None:
    result = _build_project(tmp_path)

    assert result.status == "succeeded"
    assert result.review_model_sha256
    assert result.review_manifest_path
    manifest_path = tmp_path / result.review_manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["model_sha256"] == result.review_model_sha256
    assert (manifest_path.parent / manifest["single_render"]["path"]).is_file()
    assert (manifest_path.parent / manifest["contact_sheet"]["path"]).is_file()
    assert len(manifest["views"]) == 8


def test_review_passes_with_independent_structured_reviewer(tmp_path: Path) -> None:
    execution = _build_project(tmp_path)
    result = review_cad(
        project_dir=tmp_path,
        request_text="A rectangular block.",
        model_source=(tmp_path / "code" / "model.py").read_text(encoding="utf-8"),
        execution_result=execution,
        settings=object(),
        reviewer_factory=lambda _settings: _FakeReviewer(
            {
                "status": "pass",
                "summary": "The requested block is present.",
                "findings": [],
                "checked_requirements": ["rectangular block"],
            }
        ),
    )

    assert result.status == "pass"
    assert result.checked_requirements == ("rectangular block",)
    assert (tmp_path / execution.review_artifact_dir / "result.json").is_file()


def test_reviewer_receives_every_hash_bound_python_source(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    (scaffold.code_dir / "dimensions.py").write_text(
        "REVIEW_SIDE_LENGTH = 17.0\n",
        encoding="utf-8",
    )
    scaffold.model_path.write_text(
        "from dimensions import REVIEW_SIDE_LENGTH\n"
        "import cadflow as cad\n\n"
        "def build_model(model: cad.Model):\n"
        "    return model.box(\n"
        "        width=REVIEW_SIDE_LENGTH,\n"
        "        depth=REVIEW_SIDE_LENGTH,\n"
        "        height=REVIEW_SIDE_LENGTH,\n"
        "    )\n",
        encoding="utf-8",
    )
    execution = CADExecutor().execute(tmp_path, timeout_seconds=30.0)

    class _CapturingReviewer:
        request_text = ""

        def invoke(self, messages: object, config: object = None) -> object:
            del config
            type(self).request_text = str(messages)
            return {
                "status": "pass",
                "summary": "All requested geometry is present.",
                "findings": [],
                "checked_requirements": ["cube"],
            }

    result = review_cad(
        project_dir=tmp_path,
        request_text="A 17 mm cube.",
        model_source=scaffold.model_path.read_text(encoding="utf-8"),
        execution_result=execution,
        settings=object(),
        reviewer_factory=lambda _settings: _CapturingReviewer(),
    )

    assert result.status == "pass"
    assert "code/dimensions.py" in _CapturingReviewer.request_text
    assert "REVIEW_SIDE_LENGTH = 17.0" in _CapturingReviewer.request_text


def test_review_accepts_a_deterministically_passed_assembly(tmp_path: Path) -> None:
    scaffold = create_model_source(tmp_path)
    scaffold.model_path.write_text(
        """import cadflow as cad

PRODUCT_SPEC = {
    "assumptions": [],
    "envelope": {"max_size_mm": [20.0, 5.0, 5.0]},
    "collision_exclusions": [],
}

def build_model(model: cad.Model):
    housing = cad.make_part_rpart(
        part_id="housing",
        body=cad.make_box_rsolid(width=2.0, height=2.0, depth=2.0),
    )
    shaft = cad.make_part_rpart(
        part_id="shaft",
        body=cad.make_box_rsolid(width=2.0, height=2.0, depth=2.0),
    )
    assembly = cad.make_assembly_rassembly(assembly_id="drive")
    assembly = cad.add_component_rassembly(
        assembly=assembly,
        item=housing,
        component_id="housing",
        placement=cad.identity_placement_rplacement(),
    )
    return cad.add_component_rassembly(
        assembly=assembly,
        item=shaft,
        component_id="shaft",
        placement=cad.make_placement_rplacement(origin=(10.0, 0.0, 0.0)),
    )
""",
        encoding="utf-8",
    )
    execution = CADExecutor().execute(tmp_path, timeout_seconds=30.0)
    assert execution.product_validation_status == "Passed"

    result = review_cad(
        project_dir=tmp_path,
        request_text="Two separated parts in one assembly.",
        model_source=scaffold.model_path.read_text(encoding="utf-8"),
        execution_result=execution,
        settings=object(),
        reviewer_factory=lambda _settings: _FakeReviewer(
            {
                "status": "pass",
                "summary": "Both requested components are present.",
                "findings": [],
                "checked_requirements": ["two-part assembly"],
            }
        ),
    )

    assert result.status == "pass"
    assert not any("exactly one solid" in finding.requirement for finding in result.findings)


def test_reviewer_treats_host_validation_as_authoritative(tmp_path: Path) -> None:
    execution = _build_project(tmp_path)
    manifest = json.loads(
        (tmp_path / execution.review_manifest_path).read_text(encoding="utf-8")
    )

    prompt = _review_prompt(
        "A rectangular block.",
        (tmp_path / "code" / "model.py").read_text(encoding="utf-8"),
        manifest,
        execution,
    )

    assert "trusted host executor owns strict Assembly solving" in prompt
    assert "Model Source is not required to call solve" in prompt
    assert '"product_validation_status": "Passed"' in prompt


def test_review_infrastructure_failure_is_a_structured_fail(tmp_path: Path) -> None:
    execution = ExecutionResult(
        status="succeeded",
        exit_code=0,
        error=None,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        final_shape_count=1,
        solid_count=1,
        solid_volume=1.0,
        scene_artifact_exists=True,
        scene_parse_result=SceneParseResult(valid=True),
        artifact_entries=("model.scene.zip",),
        duration_seconds=0.1,
    )

    result = review_cad(
        project_dir=tmp_path,
        request_text="Make a solid.",
        model_source="def build_model(model): ...",
        execution_result=execution,
    )

    assert result.status == "fail"
    assert any(
        finding.category == "review_infrastructure"
        and finding.severity == "blocking"
        for finding in result.findings
    )


def test_cad_review_tool_reviews_the_latest_validation(tmp_path: Path) -> None:
    execution = _build_project(tmp_path)

    class _Executor:
        def execute(self, *_args: object, **_kwargs: object) -> ExecutionResult:
            return execution

    validator = AgentModelValidator(project_dir=tmp_path, executor=_Executor())
    validator.begin_run()
    tools = {
        item.name: item
        for item in create_agent_tools(
            validator,
            request_text="A rectangular block.",
            review_settings=object(),
            reviewer_factory=lambda _settings: _FakeReviewer(
                {"status": "pass", "summary": "ok", "findings": [], "checked_requirements": []}
            ),
        )
    }

    tools["validate_model"].invoke({})
    result = tools["cad_review"].invoke({})

    assert result["status"] == "pass"
    assert any(record.tool_name == "cad_review" for record in validator.tool_use_records)


def test_reviewer_usage_is_added_to_the_conversation_log(tmp_path: Path) -> None:
    execution = _build_project(tmp_path)
    conversation_log = ConversationLog(tmp_path, turn_id="turn-1")

    class _UsageReviewer:
        def invoke(self, messages: object, config: object = None) -> object:
            assert isinstance(config, dict)
            callbacks = config["callbacks"]
            callback = callbacks[0]
            callback.on_chat_model_start(
                {"name": "reviewer"},
                [messages],
                run_id="review-model-1",
                metadata=config["metadata"],
                tags=config["tags"],
            )
            callback.on_llm_end(
                LLMResult(
                    generations=[
                        [
                            ChatGeneration(
                                message=AIMessage(
                                    content='{"status":"pass"}',
                                    usage_metadata={
                                        "input_tokens": 120,
                                        "output_tokens": 30,
                                        "total_tokens": 150,
                                        "input_token_details": {"cache_read": 20},
                                    },
                                )
                            )
                        ]
                    ]
                ),
                run_id="review-model-1",
            )
            return {
                "status": "pass",
                "summary": "ok",
                "findings": [],
                "checked_requirements": [],
            }

    result = review_cad(
        project_dir=tmp_path,
        request_text="A rectangular block.",
        model_source=(tmp_path / "code" / "model.py").read_text(encoding="utf-8"),
        execution_result=execution,
        settings=object(),
        reviewer_factory=lambda _settings: _UsageReviewer(),
        reviewer_callbacks=[conversation_log.callback_handler()],
    )

    assert result.status == "pass"
    assert conversation_log.token_usage == {
        "total_tokens": 150,
        "input_tokens": 120,
        "cached_input_tokens": 20,
        "uncached_input_tokens": 100,
        "output_tokens": 30,
    }
    assert any(
        record["payload"].get("agent_role") == "reviewer"
        for record in conversation_log.data["records"]
        if record["type"] in {"model_request", "model_response"}
    )
