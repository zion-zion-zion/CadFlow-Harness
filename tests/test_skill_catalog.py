from __future__ import annotations

import re
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.middleware.skills import SkillsMiddleware


def test_deep_agent_discovers_every_packaged_skill() -> None:
    repo_root = Path(__file__).parents[1].resolve()
    skill_root = repo_root / "skills"
    expected_names = {
        path.parent.name for path in skill_root.glob("*/SKILL.md") if path.is_file()
    }
    middleware = SkillsMiddleware(
        backend=LocalShellBackend(root_dir=repo_root, virtual_mode=False),
        sources=[str(skill_root)],
    )

    update = middleware.before_agent({}, None, {})  # type: ignore[arg-type]

    assert update is not None
    discovered = update["skills_metadata"]
    assert {skill["name"] for skill in discovered} == expected_names
    assert all(Path(skill["path"]).is_file() for skill in discovered)


def test_packaged_skill_references_exist() -> None:
    skill_root = Path(__file__).parents[1] / "skills"

    for skill_file in skill_root.glob("*/SKILL.md"):
        source = skill_file.read_text(encoding="utf-8")
        for relative_path in re.findall(r"`(references/[^`]+)`", source):
            assert (skill_file.parent / relative_path).is_file(), (
                f"{skill_file.relative_to(skill_root)} references missing "
                f"{relative_path}"
            )


def test_assembly_skill_has_no_collision_validation_contract() -> None:
    skill_root = Path(__file__).parents[1] / "skills" / "cadflow-model-assembly"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            skill_root / "SKILL.md",
            skill_root / "references" / "assembly-api.md",
            skill_root / "references" / "project-structure.md",
        )
    ).lower()

    for removed_term in ("collision", "penetration", "interference"):
        assert removed_term not in source


def test_current_example_index_references_existing_paths() -> None:
    examples_root = Path(__file__).parents[1] / "examples"
    index = (examples_root / "README.md").read_text(encoding="utf-8")
    referenced_paths = re.findall(
        r"`((?:cadflow_[^`]+\.py|[0-9]+_[^`]+/))`",
        index,
    )

    assert referenced_paths
    for relative_path in referenced_paths:
        assert (examples_root / relative_path).exists(), relative_path
