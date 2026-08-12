from pathlib import Path

from backend.__main__ import load_backend_environment
from backend.agent import AgentSettings


def test_backend_entrypoint_loads_dotenv_without_overriding_environment(
    tmp_path: Path, monkeypatch,
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "OPENAI_API_KEY=file-key\n"
        "OPENAI_MODEL_ID=file-model\n"
        "OPENAI_BASE_URL=https://provider.invalid/v1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_ID", "exported-model")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    load_backend_environment(dotenv_path)
    settings = AgentSettings.from_environment()

    assert settings.api_key == "file-key"
    assert settings.model_id == "exported-model"
    assert settings.base_url == "https://provider.invalid/v1"
