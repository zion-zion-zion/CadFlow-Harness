from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.cad_executor import CADExecutor
from backend.model_source import create_model_source
from backend.product_artifact import ProductArtifactStatus
from backend.projects import (
    AgentHarness,
    MAX_PROMPT_CHARS,
    ProjectState,
    ProjectStateError,
    PromptValidationError,
    ProjectStore,
)


def test_prompt_submission_is_persisted_across_multiple_turns(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Flange")

    assert (tmp_path / project.project_id / "code" / "model.py").is_file()
    assert (tmp_path / project.project_id / "artifacts").is_dir()

    running = store.submit_prompt(project.project_id, "Create a round flange.")

    assert running.state is ProjectState.RUNNING
    assert running.prompt == "Create a round flange."
    assert (tmp_path / project.project_id / "prompt.txt").read_text() == (
        "Create a round flange."
    )

    reloaded = ProjectStore(tmp_path).get_project(project.project_id)
    assert reloaded.state is ProjectState.RUNNING
    assert reloaded.prompt == "Create a round flange."

    store.mark_failed(project.project_id, "first turn failed")
    second = store.submit_prompt(project.project_id, "A second Prompt.")
    assert second.state is ProjectState.RUNNING
    assert second.prompt == "A second Prompt."


def test_run_uses_deepagents_harness(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Flange")
    running = store.submit_prompt(project.project_id, "Create a flange.")

    assert running.harness is AgentHarness.DEEPAGENTS
    metadata = (tmp_path / project.project_id / "project.json").read_text()
    assert '"harness": "deepagents"' in metadata
    reloaded = ProjectStore(tmp_path).get_project(project.project_id)
    assert reloaded.harness is AgentHarness.DEEPAGENTS


def test_retired_harness_metadata_loads_as_deepagents(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Legacy")
    metadata_path = tmp_path / project.project_id / "project.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["harness"] = "retired"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    loaded = store.get_project(project.project_id)

    assert loaded.harness is AgentHarness.DEEPAGENTS


def test_project_names_may_repeat_but_ids_are_opaque(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)

    first = store.create_project("same name")
    second = store.create_project("same name")

    assert first.project_id != second.project_id
    assert "/" not in first.project_id
    assert "\\" not in first.project_id
    assert {item.project_id for item in store.list_projects()} == {
        first.project_id,
        second.project_id,
    }


@pytest.mark.parametrize("prompt", ["", "   ", "x" * (MAX_PROMPT_CHARS + 1)])
def test_invalid_prompt_is_rejected_before_running(tmp_path: Path, prompt: str) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Draft")

    with pytest.raises(PromptValidationError):
        store.submit_prompt(project.project_id, prompt)

    assert store.get_project(project.project_id).state is ProjectState.DRAFT
    assert not (tmp_path / project.project_id / "prompt.txt").exists()


def test_terminal_project_can_start_a_follow_up_turn(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Draft")
    store.submit_prompt(project.project_id, "Make a box.")
    store.mark_failed(project.project_id, "first CAD execution failed")

    restarted = store.submit_prompt(project.project_id, "Try again.")
    assert restarted.state is ProjectState.RUNNING
    assert restarted.failure_reason is None


def test_follow_up_without_token_usage_preserves_project_total(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Usage history")
    store.submit_prompt(project.project_id, "Make a box.")
    store.mark_failed(
        project.project_id,
        "first turn failed",
        {
            "status": "failed",
            "token_usage": {
                "input_tokens": 20,
                "cached_input_tokens": 5,
                "uncached_input_tokens": 15,
                "output_tokens": 10,
                "total_tokens": 30,
            },
        },
    )

    store.submit_prompt(project.project_id, "Try again.")
    store.mark_failed(
        project.project_id,
        "second turn failed",
        {"status": "failed", "token_usage": None},
    )

    diagnostics = store.read_diagnostics(project.project_id)
    assert diagnostics is not None
    assert diagnostics["status"] == "failed"
    assert diagnostics["token_usage"] == {
        "total_tokens": 30,
        "input_tokens": 20,
        "cached_input_tokens": 5,
        "uncached_input_tokens": 15,
        "output_tokens": 10,
    }


def test_restart_recovery_does_not_count_previous_token_usage_twice(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Interrupted follow-up")
    store.submit_prompt(project.project_id, "Make a box.")
    store.mark_failed(
        project.project_id,
        "first turn failed",
        {
            "token_usage": {
                "input_tokens": 20,
                "cached_input_tokens": 5,
                "output_tokens": 10,
            }
        },
    )
    store.submit_prompt(project.project_id, "Try again after restart.")

    reloaded = ProjectStore(tmp_path)
    reloaded.recover_interrupted_runs()

    diagnostics = reloaded.read_diagnostics(project.project_id)
    assert diagnostics is not None
    assert diagnostics["token_usage"] == {
        "total_tokens": 30,
        "input_tokens": 20,
        "cached_input_tokens": 5,
        "uncached_input_tokens": 15,
        "output_tokens": 10,
    }


def test_success_requires_a_complete_product_artifact(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Result")
    store.submit_prompt(project.project_id, "Make a box.")

    with pytest.raises(ProjectStateError, match="complete product artifact"):
        store.mark_succeeded(project.project_id)

    artifact_dir = tmp_path / project.project_id / "artifacts"
    (artifact_dir / "model.scene.zip").write_bytes(b"validated elsewhere")
    with pytest.raises(ProjectStateError, match="complete product artifact"):
        store.mark_succeeded(project.project_id)


def test_success_promotes_a_complete_product_bundle_to_an_accepted_version(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Accepted product")
    store.submit_prompt(project.project_id, "Make a box.")
    project_dir = tmp_path / project.project_id
    scaffold = create_model_source(project_dir)
    scaffold.model_path.write_text(
        "import cadflow as cad\n\n"
        "def build_model(model: cad.Model):\n"
        "    return model.box(width=2.0, depth=3.0, height=4.0)\n",
        encoding="utf-8",
    )
    execution = CADExecutor().execute(project_dir, timeout_seconds=30.0)
    assert execution.is_validated_product
    diagnostics = {
        "execution_result": execution.to_dict(),
        "review_result": {
            "status": "pass",
            "summary": "The requested box is present.",
            "model_sha256": execution.review_model_sha256,
            "reviewer_version": "cad-review-v1",
            "checked_requirements": ["box"],
            "evidence_hashes": {},
        },
    }

    succeeded = store.mark_succeeded(project.project_id, diagnostics)

    assert succeeded.state is ProjectState.SUCCEEDED
    current = json.loads(
        (project_dir / "current.json").read_text(encoding="utf-8")
    )
    assert current["schema_version"] == "cadflow-project-artifact/v1"
    assert current["version"] == 1
    assert current["status"] == "Accepted"
    assert current["result_kind"] == "part"
    assert current["product_manifest"] == "artifacts/v0001/files/product.json"
    artifact = store.product_artifact(project.project_id)
    assert artifact.status is ProductArtifactStatus.ACCEPTED
    assert artifact.file_path("scene") == store.scene_artifact(project.project_id)


def test_failed_follow_up_restores_the_previous_source_tree(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Versioned source")
    project_dir = tmp_path / project.project_id
    model_path = project_dir / "code" / "model.py"
    first_source = (
        "FIRST = True\n"
        "from helper import SIZE\n"
        "import cadflow as cad\n\n"
        "def build_model(model: cad.Model):\n"
        "    return model.box(width=SIZE, depth=SIZE, height=SIZE)\n"
    )
    model_path.write_text(first_source, encoding="utf-8")
    (project_dir / "code" / "helper.py").write_text(
        "SIZE = 2.0\n",
        encoding="utf-8",
    )
    store.submit_prompt(project.project_id, "Create the first model.")
    execution = CADExecutor().execute(project_dir, timeout_seconds=30.0)
    store.mark_succeeded(
        project.project_id,
        {
            "execution_result": execution.to_dict(),
            "review_result": {
                "status": "pass",
                "summary": "The requested model is present.",
                "model_sha256": execution.review_model_sha256,
            },
        },
    )
    first_scene = store.scene_artifact(project.project_id).read_bytes()

    version_root = project_dir / "artifacts" / "v0001"
    assert (version_root / "source" / "code" / "model.py").is_file()
    assert (version_root / "source" / "code" / "helper.py").is_file()
    assert not (version_root / "source" / "model.py").exists()

    store.submit_prompt(project.project_id, "Try a follow-up.")
    model_path.write_text("BROKEN = True\n", encoding="utf-8")
    added_source = project_dir / "code" / "new_helper.py"
    added_source.write_text("BROKEN_HELPER = True\n", encoding="utf-8")
    store.mark_failed(project.project_id, "follow-up failed")

    assert model_path.read_text(encoding="utf-8") == first_source
    assert not added_source.exists()
    assert store.scene_artifact(project.project_id).read_bytes() == first_scene
