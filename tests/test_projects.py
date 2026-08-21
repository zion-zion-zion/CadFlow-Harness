from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    assert (tmp_path / project.project_id / "model.py").is_file()
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


def test_success_requires_the_canonical_scene_artifact(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Result")
    store.submit_prompt(project.project_id, "Make a box.")

    with pytest.raises(ProjectStateError, match="canonical Scene Artifact"):
        store.mark_succeeded(project.project_id)

    artifact_dir = tmp_path / project.project_id / "artifacts"
    (artifact_dir / "model.scene.zip").write_bytes(b"validated elsewhere")
    succeeded = store.mark_succeeded(project.project_id)

    assert succeeded.state is ProjectState.SUCCEEDED
    assert store.scene_artifact(project.project_id).name == "model.scene.zip"
    assert store.current_result_kind(project.project_id) == "part"


def test_success_persists_the_validated_result_kind(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Assembly result")
    store.submit_prompt(project.project_id, "Create an assembly.")
    artifact_dir = tmp_path / project.project_id / "artifacts"
    (artifact_dir / "model.scene.zip").write_bytes(b"validated elsewhere")

    store.mark_succeeded(
        project.project_id,
        {"execution_result": {"result_kind": "assembly"}},
    )

    current = json.loads(
        (tmp_path / project.project_id / "current.json").read_text(encoding="utf-8")
    )
    assert current["result_kind"] == "assembly"
    assert store.current_result_kind(project.project_id) == "assembly"


def test_failed_follow_up_restores_the_previous_source_tree(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Versioned source")
    project_dir = tmp_path / project.project_id
    model_path = project_dir / "model.py"
    model_path.write_text("FIRST = True\n", encoding="utf-8")
    store.submit_prompt(project.project_id, "Create the first model.")
    (project_dir / "artifacts" / "model.scene.zip").write_bytes(b"first")
    store.mark_succeeded(project.project_id)

    store.submit_prompt(project.project_id, "Try a follow-up.")
    model_path.write_text("BROKEN = True\n", encoding="utf-8")
    added_source = project_dir / "new_helper.py"
    added_source.write_text("BROKEN_HELPER = True\n", encoding="utf-8")
    store.mark_failed(project.project_id, "follow-up failed")

    assert model_path.read_text(encoding="utf-8") == "FIRST = True\n"
    assert not added_source.exists()
    assert store.scene_artifact(project.project_id).read_bytes() == b"first"
