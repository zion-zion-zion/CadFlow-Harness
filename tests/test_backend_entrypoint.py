from pathlib import Path

from backend import __main__ as entrypoint
from backend.__main__ import load_backend_environment
from backend.agent import AgentSettings


def test_backend_entrypoint_loads_dotenv_without_overriding_environment(
    tmp_path: Path, monkeypatch,
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "OPENAI_API_KEY=file-key\n"
        "OPENAI_MODEL_ID=file-model\n"
        "OPENAI_BASE_URL=https://provider.invalid/v1\n"
        "OPENAI_REASONING_EFFORT=medium\n"
        "CADFLOW_AGENT_RUN_TIMEOUT_SECONDS=45\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_ID", "exported-model")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("CADFLOW_AGENT_RUN_TIMEOUT_SECONDS", raising=False)

    load_backend_environment(dotenv_path)
    settings = AgentSettings.from_environment()

    assert settings.api_key == "file-key"
    assert settings.model_id == "exported-model"
    assert settings.base_url == "https://provider.invalid/v1"
    assert settings.reasoning_effort == "medium"
    assert settings.run_timeout_seconds == 45.0


def test_backend_entrypoint_uses_configured_host(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setenv("TEXT_TO_CAD_HOST", "0.0.0.0")
    monkeypatch.setattr(entrypoint, "load_backend_environment", lambda: None)
    monkeypatch.setattr(
        entrypoint.uvicorn,
        "run",
        lambda app, **kwargs: captured.update(app=app, **kwargs),
    )

    entrypoint.main()

    assert captured["host"] == "0.0.0.0"
