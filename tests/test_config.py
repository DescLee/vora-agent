from pathlib import Path

import pytest

from manus_mini.config import AppConfig, load_dotenv
from manus_mini.llm import OpenAICompatibleLLMClient, get_default_llm_client


def clear_llm_env_vars(monkeypatch) -> None:
    for key in [
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_ATTEMPTS",
        "LLM_RETRY_BACKOFF_SECONDS",
    ]:
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


def test_load_dotenv_supports_export_prefix_and_inline_comments(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "export LLM_PROVIDER=openai-compatible # provider comment",
                "LLM_MODEL='qwen turbo' # model comment",
                'LLM_BASE_URL="http://localhost:1234/v1#not-comment"',
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_dotenv(env_path)

    assert loaded["LLM_PROVIDER"] == "openai-compatible"
    assert loaded["LLM_MODEL"] == "qwen turbo"
    assert loaded["LLM_BASE_URL"] == "http://localhost:1234/v1#not-comment"


def test_app_config_reads_env_file(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai-compatible\n"
        "LLM_BASE_URL=http://localhost:1234/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=qwen-turbo\n"
        "LLM_TIMEOUT_SECONDS=15\n"
        "LLM_MAX_ATTEMPTS=5\n"
        "LLM_RETRY_BACKOFF_SECONDS=0.5\n",
        encoding="utf-8",
    )

    config = AppConfig.from_env(env_path)

    assert config.llm_provider == "openai-compatible"
    assert config.llm_base_url == "http://localhost:1234/v1"
    assert config.llm_api_key == "test-key"
    assert config.llm_model == "qwen-turbo"
    assert config.llm_timeout_seconds == 15
    assert config.llm_max_attempts == 5
    assert config.llm_retry_backoff_seconds == 0.5
    assert config.llm_config_source == str(env_path)


def test_app_config_falls_back_to_user_env_when_project_env_is_missing(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    home = tmp_path / "home"
    user_env = home / ".manus-mini" / ".env"
    user_env.parent.mkdir(parents=True)
    user_env.write_text(
        "LLM_PROVIDER=openai-compatible\n"
        "LLM_BASE_URL=http://localhost:1234/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=qwen-user\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    config = AppConfig.from_env(tmp_path / "other-project" / ".env")

    assert config.llm_provider == "openai-compatible"
    assert config.llm_base_url == "http://localhost:1234/v1"
    assert config.llm_api_key == "test-key"
    assert config.llm_model == "qwen-user"
    assert config.llm_config_source == str(user_env)


def test_app_config_falls_back_to_package_env_when_project_and_user_env_are_missing(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    package_env = tmp_path / "package" / ".env"
    package_env.parent.mkdir()
    package_env.write_text(
        "LLM_PROVIDER=openai-compatible\n"
        "LLM_BASE_URL=http://localhost:1234/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=qwen-package\n",
        encoding="utf-8",
    )

    config = AppConfig.from_env(
        tmp_path / "other-project" / ".env",
        user_env_path=tmp_path / "home" / ".manus-mini" / ".env",
        package_env_path=package_env,
    )

    assert config.llm_provider == "openai-compatible"
    assert config.llm_base_url == "http://localhost:1234/v1"
    assert config.llm_api_key == "test-key"
    assert config.llm_model == "qwen-package"
    assert config.llm_config_source == str(package_env)


def test_app_config_defaults_to_longer_llm_timeout(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    config = AppConfig.from_env(tmp_path / ".env")

    assert config.llm_timeout_seconds == 120
    assert config.llm_max_attempts == 3
    assert config.llm_retry_backoff_seconds == 0.25


def test_get_default_llm_client_requires_explicit_provider(tmp_path: Path, monkeypatch) -> None:
    clear_llm_env_vars(monkeypatch)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    config = AppConfig.from_env(
        tmp_path / ".env",
        user_env_path=tmp_path / "home" / ".manus-mini" / ".env",
        package_env_path=tmp_path / "package" / ".env",
    )

    with pytest.raises(RuntimeError, match="LLM_PROVIDER must be set to openai-compatible"):
        get_default_llm_client(config)


def test_get_default_llm_client_uses_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    client = get_default_llm_client()

    assert isinstance(client, OpenAICompatibleLLMClient)
