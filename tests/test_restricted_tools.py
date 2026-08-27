from pathlib import Path

from backend.restricted_tools import AgentModelValidator, RestrictedAgentTools


class StubExecutor:
    def execute(self, project_dir: Path, **kwargs: object) -> str:
        assert project_dir.is_dir()
        assert "cancellation_token" in kwargs
        return "executed"


def test_validator_prepares_and_executes_without_reference_gates(tmp_path: Path) -> None:
    validator = AgentModelValidator(project_dir=tmp_path, executor=StubExecutor())

    scaffold = validator.begin_run()
    assert scaffold.model_path == tmp_path / "code" / "model.py"
    assert validator.validate_model() == "executed"
    assert [record.tool_name for record in validator.tool_use_records] == [
        "prepare_model_source",
        "validate_model",
    ]


def test_begin_run_does_not_overwrite_existing_model_source(tmp_path: Path) -> None:
    validator = AgentModelValidator(project_dir=tmp_path)
    scaffold = validator.begin_run()
    scaffold.model_path.write_text("custom source\n", encoding="utf-8")

    validator.begin_run()

    assert scaffold.model_path.read_text(encoding="utf-8") == "custom source\n"


def test_legacy_name_is_only_a_compatibility_alias() -> None:
    assert RestrictedAgentTools is AgentModelValidator
