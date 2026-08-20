from __future__ import annotations

import time
from pathlib import Path

import cadflow as cad

from backend.live_preview import (
    LivePreviewExecutor,
    LivePreviewResult,
    LivePreviewScheduler,
    LivePreviewStore,
)
from backend.projects import ProjectStore


def _model_source(width: float) -> str:
    return f"""from pathlib import Path
import cadflow as cad

def build_model(model: cad.Model):
    Path("preview-side-effect.txt").write_text("snapshot only", encoding="utf-8")
    return model.box(width={width}, depth=4.0, height=3.0)
"""


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def test_live_preview_executor_isolated_from_validation_outputs(tmp_path: Path) -> None:
    (tmp_path / "model.py").write_text(_model_source(5.0), encoding="utf-8")

    result = LivePreviewExecutor().execute(tmp_path, timeout_seconds=10.0)

    assert result.status == "succeeded"
    assert result.payload is not None and result.payload[:4] == b"glTF"
    assert not (tmp_path / "preview-side-effect.txt").exists()
    assert not (tmp_path / "artifacts").exists()
    assert not (tmp_path / ".cad-review").exists()


def test_live_preview_store_keeps_last_model_when_next_build_fails(
    tmp_path: Path,
) -> None:
    with cad.Model() as model:
        payload = model.box(width=2.0, depth=2.0, height=2.0).preview_glb()
    store = LivePreviewStore(tmp_path)

    current = store.publish(payload, "first-hash")
    failed = store.write_status("failed", source_hash="second-hash", error="broken")

    assert current.revision == 1
    assert failed.revision == 1
    assert failed.artifact_available is True
    assert store.artifact().read_bytes() == payload


def test_scheduler_rebuilds_after_project_python_changes(tmp_path: Path) -> None:
    projects = ProjectStore(tmp_path)
    project = projects.create_project("Live")
    project = projects.submit_prompt(project.project_id, "Create a box")
    project_dir = projects.project_directory(project.project_id)
    project_dir.joinpath("model.py").write_text(_model_source(3.0), encoding="utf-8")
    with cad.Model() as model:
        payload = model.box(width=1.0, depth=1.0, height=1.0).preview_glb()

    class RecordingExecutor:
        def __init__(self) -> None:
            self.sources: list[str] = []

        def execute(self, root: Path, **_kwargs: object) -> LivePreviewResult:
            self.sources.append(Path(root, "model.py").read_text(encoding="utf-8"))
            return LivePreviewResult("succeeded", payload=payload)

    executor = RecordingExecutor()
    scheduler = LivePreviewScheduler(
        projects,
        executor=executor,
        debounce_seconds=0.02,
        poll_seconds=0.01,
    )
    try:
        scheduler.activate(project.project_id)
        store = LivePreviewStore(project_dir)
        _wait_until(lambda: store.read_status().revision == 1)

        project_dir.joinpath("model.py").write_text(_model_source(9.0), encoding="utf-8")
        _wait_until(lambda: store.read_status().revision == 2)

        assert len(executor.sources) == 2
        assert "width=9.0" in executor.sources[-1]
        assert store.read_status().state == "current"
    finally:
        scheduler.close()
