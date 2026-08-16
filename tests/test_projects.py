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


def test_prompt_submission_is_persisted_and_one_shot(tmp_path: Path) -> None:
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

    with pytest.raises(ProjectStateError):
        store.submit_prompt(project.project_id, "A second Prompt.")


def test_harness_round_trips_and_legacy_project_defaults_to_deepagents(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Pi flange")
    running = store.submit_prompt(project.project_id, "Create a flange.", "pi")

    assert running.harness is AgentHarness.PI
    metadata = (tmp_path / project.project_id / "project.json").read_text()
    assert '"harness": "pi"' in metadata
    assert ProjectStore(tmp_path).get_project(project.project_id).harness is AgentHarness.PI

    legacy = store.create_project("Legacy")
    store.submit_prompt(legacy.project_id, "Create a legacy part.")
    legacy_metadata = tmp_path / legacy.project_id / "project.json"
    legacy_data = json.loads(legacy_metadata.read_text(encoding="utf-8"))
    del legacy_data["harness"]
    legacy_metadata.write_text(
        json.dumps(legacy_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    before_load = legacy_metadata.read_bytes()

    loaded = ProjectStore(tmp_path).get_project(legacy.project_id)

    assert loaded.state is ProjectState.RUNNING
    assert loaded.harness is AgentHarness.DEEPAGENTS
    assert legacy_metadata.read_bytes() == before_load


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


def test_terminal_project_cannot_be_run_again(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Draft")
    store.submit_prompt(project.project_id, "Make a box.")
    store.mark_failed(project.project_id, "first CAD execution failed")

    with pytest.raises(ProjectStateError):
        store.submit_prompt(project.project_id, "Try again.")


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
