from pathlib import Path

from manus_mini.config import AppConfig, load_dotenv
from manus_mini.llm import MockLLMClient, OpenAICompatibleLLMClient, get_default_llm_client


def clear_llm_env_vars(monkeypatch) -> None:
    for key in ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT_SECONDS"]:
        monkeypatch.delenv(key, raising=False)


def test_load_dotenv_reads_llm_settings(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "LLM_PROVIDER=openai-compatible",
                "LLM_BASE_URL=http://localhost:1234/v1",
                "LLM_API_KEY=test-key",
                "LLM_MODEL=test-model",
                "LLM_TIMEOUT_SECONDS=45",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_dotenv(env_path)

    assert loaded["LLM_PROVIDER"] == "openai-compatible"
    assert loaded["LLM_MODEL"] == "test-model"


def test_app_config_reads_env_file(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai-compatible\n"
        "LLM_BASE_URL=http://localhost:1234/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=qwen-turbo\n"
        "LLM_TIMEOUT_SECONDS=15\n",
        encoding="utf-8",
    )

    config = AppConfig.from_env(env_path)

    assert config.llm_provider == "openai-compatible"
    assert config.llm_base_url == "http://localhost:1234/v1"
    assert config.llm_api_key == "test-key"
    assert config.llm_model == "qwen-turbo"
    assert config.llm_timeout_seconds == 15


def test_get_default_llm_client_uses_mock_when_not_configured(monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)

    client = get_default_llm_client()

    assert isinstance(client, MockLLMClient)


def test_get_default_llm_client_uses_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    client = get_default_llm_client()

    assert isinstance(client, OpenAICompatibleLLMClient)
