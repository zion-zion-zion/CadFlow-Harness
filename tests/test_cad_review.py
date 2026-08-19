from __future__ import annotations

import json
from pathlib import Path

from backend.cad_executor import CADExecutor, ExecutionResult
from backend.cad_review import review_cad
from backend.agent import create_agent_tools
from backend.model_source import create_model_source
from backend.restricted_tools import AgentModelValidator
from backend.scene_validation import SceneParseResult


class _FakeReviewer:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def invoke(self, _messages: object) -> object:
        return self.payload


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
        model_source=(tmp_path / "model.py").read_text(encoding="utf-8"),
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
