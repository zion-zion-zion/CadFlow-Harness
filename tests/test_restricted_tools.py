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
    assert scaffold.model_path == tmp_path / "model.py"
    assert validator.validate_model() == "executed"
    assert [record.tool_name for record in validator.tool_use_records] == [
        "prepare_model_source",
        "validate_model",
    ]


def test_legacy_name_is_only_a_compatibility_alias() -> None:
    assert RestrictedAgentTools is AgentModelValidator
