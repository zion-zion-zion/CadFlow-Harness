from __future__ import annotations

from pathlib import Path

from backend.agent_backend import create_agent_backend


def test_agent_backend_mounts_project_and_read_only_skills(tmp_path: Path) -> None:
    project = tmp_path / "project"
    skills = tmp_path / "skills"
    project.mkdir()
    code = project / "code"
    code.mkdir()
    skills.mkdir()
    (code / "model.py").write_text("model = 1\n", encoding="utf-8")
    (skills / "SKILL.md").write_text("skill\n", encoding="utf-8")

    backend = create_agent_backend(
        project,
        skill_root=skills,
    )

    assert backend.read("/code/model.py").error is None
    assert backend.read("/skills/SKILL.md").error is None
    assert backend.read(str(tmp_path / "examples" / "example.py")).error is not None

    project_write = backend.write("/code/helper.py", "helper = 1\n")
    assert project_write.error is None
    assert (code / "helper.py").read_text(encoding="utf-8") == "helper = 1\n"

    reference_write = backend.write("/skills/SKILL.md", "mutated\n")
    assert reference_write.error == "This reference directory is read-only"
    assert (skills / "SKILL.md").read_text(encoding="utf-8") == "skill\n"


def test_agent_backend_rejects_reserved_and_outside_paths(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    code = project / "code"
    code.mkdir()
    (project / "artifacts").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    backend = create_agent_backend(project)

    assert backend.write("/code/.env", "SECRET=x\n").error == (
        "Environment files cannot be edited by the Agent"
    )
    assert backend.write("/code/artifacts/x.py", "artifact\n").error == (
        "This Project path is reserved and cannot be edited by the Agent"
    )
    assert backend.write("/code/notes.txt", "notes\n").error == (
        "The Agent may write only Python files inside /code"
    )
    assert backend.read(str(outside)).error is not None
    assert backend.read("/conversation.jsonl").error is not None
    assert backend.delete("/code/model.py").error == (
        "delete is not available to the Agent"
    )


def test_agent_backend_lists_and_searches_only_python_source(tmp_path: Path) -> None:
    project = tmp_path / "project"
    code = project / "code"
    nested = code / "nested"
    nested.mkdir(parents=True)
    (code / "model.py").write_text("MODEL = 1\n", encoding="utf-8")
    (nested / "helper.py").write_text("HELPER = 1\n", encoding="utf-8")
    (code / "notes.txt").write_text("do not expose\n", encoding="utf-8")
    (nested / "debug.json").write_text("do not expose\n", encoding="utf-8")
    (project / "conversation.jsonl").write_text("do not expose\n", encoding="utf-8")
    (project / "runtime.py").write_text("do not expose\n", encoding="utf-8")
    backend = create_agent_backend(project)

    root_entries = backend.ls("/").entries or []
    assert {entry["path"] for entry in root_entries} == {"/code/"}
    code_entries = backend.ls("/code/").entries or []
    assert {entry["path"] for entry in code_entries} == {
        "/code/model.py",
        "/code/nested/",
    }

    glob_matches = backend.glob("**/*", "/code/").matches or []
    assert {match["path"] for match in glob_matches} == {
        "/code/model.py",
        "/code/nested/helper.py",
    }
    grep_matches = backend.grep("do not expose", "/code/").matches or []
    assert grep_matches == []
    assert backend.read("/code/notes.txt").error is not None
    assert backend.read("/conversation.jsonl").error is not None

    shell_result = backend.routes["/code/"].execute("cat /etc/passwd")  # type: ignore[attr-defined]
    assert shell_result.exit_code == 1
    assert "disabled" in shell_result.output.lower()


def test_agent_backend_rejects_source_symlinks_that_escape_code(tmp_path: Path) -> None:
    project = tmp_path / "project"
    code = project / "code"
    code.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = True\n", encoding="utf-8")
    (code / "linked.py").symlink_to(outside)
    backend = create_agent_backend(project)

    assert backend.read("/code/linked.py").error is not None
    assert backend.write("/code/linked.py", "SECRET = False\n").error is not None
    assert backend.glob("**/*.py", "/code/").matches == []


def test_agent_backend_rejects_a_symlinked_code_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "code").symlink_to(outside, target_is_directory=True)

    try:
        create_agent_backend(project)
    except ValueError as error:
        assert "must not be a symlink" in str(error)
    else:
        raise AssertionError("a symlinked Project code directory must be rejected")
