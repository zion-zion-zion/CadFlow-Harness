from pathlib import Path

import pytest

from backend.restricted_tools import ReferenceContractError, RestrictedAgentTools


class StubExecutor:
    def execute(self, project_dir: Path, **kwargs: object) -> str:
        assert project_dir.is_dir()
        return "executed"


def test_agent_tools_enforce_reference_order_and_record_usage(tmp_path: Path) -> None:
    tools = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
        executor=StubExecutor(),
    )

    tools.begin_run()
    with pytest.raises(ReferenceContractError):
        tools.read_api_doc("model")

    tools.read_skill_entry()
    tools.read_api_index()
    tools.read_stdlib_index()
    assert "def model" in tools.read_api_doc("model")
    assert "def capture_result" in tools.read_api_doc("capture_result")
    assert "def make_box_rsolid" in tools.read_api_doc("make_box_rsolid")
    assert "10_part_assembly.py" in tools.list_examples()
    assert "@scad.model" in tools.read_example("10_part_assembly.py")

    assert "Single-part SimpleCADAPI Model Source" in tools.read_model_source()
    tools.write_model_source("# edited by the Agent\n")
    assert tools.read_model_source() == "# edited by the Agent\n"
    assert tools.execute_model(
        api_names=("model", "capture_result", "make_box_rsolid")
    ) == "executed"

    records = tools.tool_use_records
    assert [record.tool_name for record in records] == [
        "prepare_model_source",
        "read_skill_entry",
        "read_api_index",
        "read_stdlib_index",
        "read_api_doc",
        "read_api_doc",
        "read_api_doc",
        "list_examples",
        "read_example",
        "read_model_source",
        "write_model_source",
        "read_model_source",
        "execute_model",
    ]
    assert records[4].target == "skills/simplecadapi/references/docs/api/model.md"
    assert records[-1].target == "model.py"
    assert records[-1].reference_names == (
        "model",
        "capture_result",
        "make_box_rsolid",
    )


def test_agent_tools_do_not_offer_general_or_cross_project_file_access(
    tmp_path: Path,
) -> None:
    tools = RestrictedAgentTools(
        repo_root=Path(__file__).parents[1],
        project_dir=tmp_path,
        executor=StubExecutor(),
    )

    assert not hasattr(tools, "run_shell")
    tools.read_skill_entry()
    tools.read_api_index()
    tools.read_stdlib_index()
    with pytest.raises(ReferenceContractError):
        tools.read_example("../CONTEXT.md")

    with pytest.raises(ReferenceContractError):
        tools.read_api_doc("../model")
