from __future__ import annotations

from pathlib import Path

from deepagents.backends import LocalShellBackend

from backend.agent_backend import create_agent_backend


def test_agent_backend_mounts_project_and_read_only_skills(tmp_path: Path) -> None:
    project = tmp_path / "project"
    skills = tmp_path / "skills"
    project.mkdir()
    skills.mkdir()
    (project / "model.py").write_text("model = 1\n", encoding="utf-8")
    (skills / "SKILL.md").write_text("skill\n", encoding="utf-8")

    backend = create_agent_backend(
        project,
        skill_root=skills,
    )

    assert isinstance(backend.default, LocalShellBackend)
    assert backend.read(str(project / "model.py")).error is None
    assert backend.read(str(skills / "SKILL.md")).error is None
    assert backend.read(str(tmp_path / "examples" / "example.py")).error is not None

    project_write = backend.write(str(project / "helper.py"), "helper = 1\n")
    assert project_write.error is None
    assert (project / "helper.py").read_text(encoding="utf-8") == "helper = 1\n"

    reference_write = backend.write(str(skills / "SKILL.md"), "mutated\n")
    assert reference_write.error == "This reference directory is read-only"
    assert (skills / "SKILL.md").read_text(encoding="utf-8") == "skill\n"


def test_agent_backend_rejects_reserved_and_outside_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "artifacts").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    backend = create_agent_backend(project)

    assert backend.write(str(project / ".env"), "SECRET=x\n").error == (
        "Environment files cannot be edited by the Agent"
    )
    assert backend.write(str(project / "artifacts" / "x.txt"), "artifact\n").error == (
        "This Project path is reserved and cannot be edited by the Agent"
    )
    assert backend.write(str(project / "notes.txt"), "notes\n").error == (
        "The Agent may write only model.py or Project-local Python modules"
    )
    assert backend.read(str(outside)).error is not None
    assert backend.delete(str(project / "model.py")).error == (
        "delete is not available to the Agent"
    )
