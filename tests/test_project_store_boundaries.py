from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.project_artifacts import ArtifactVersionStore
from backend.project_diagnostics import DiagnosticsStore
from backend.project_metadata import ProjectMetadataStore
from backend.projects import ProjectNotFoundError, ProjectStateError, ProjectStore


def test_project_store_is_the_only_locked_persistence_facade(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)

    assert isinstance(store._lock, type(threading.RLock()))
    assert not hasattr(store._metadata, "_lock")
    assert not hasattr(store._diagnostics, "_lock")
    artifact_store = ArtifactVersionStore(
        tmp_path, version_limit=store.artifact_version_limit
    )
    assert not hasattr(artifact_store, "_lock")


def test_catalog_discovery_keeps_hexadecimal_project_id_boundary(tmp_path: Path) -> None:
    non_project = tmp_path / ("z" * 32)
    non_project.mkdir()
    (non_project / "project.json").write_text("{}", encoding="utf-8")

    store = ProjectStore(tmp_path)

    assert store.list_projects() == ()
    with pytest.raises(ProjectNotFoundError, match="invalid Project ID"):
        store.project_directory("z" * 32)


def test_internal_metadata_and_diagnostics_helpers_share_no_store_state(
    tmp_path: Path,
) -> None:
    metadata = ProjectMetadataStore(tmp_path)
    diagnostics = DiagnosticsStore()

    assert metadata.root == tmp_path.resolve()
    assert diagnostics.read(tmp_path) is None


def test_success_publication_requires_a_running_turn(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("No duplicate version")

    with pytest.raises(ProjectStateError, match="only Running Projects can finish"):
        store.mark_succeeded(project.project_id)

    assert store.current_artifact_version(project.project_id) is None


def test_delete_rejects_a_catalog_entry_symlink(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create_project("Symlink target")
    alias_id = "a" * 32
    (tmp_path / alias_id).symlink_to(store.project_directory(project.project_id))

    with pytest.raises(ProjectNotFoundError):
        store.delete_project(alias_id)

    assert store.get_project(project.project_id).name == "Symlink target"
