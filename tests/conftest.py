import pytest


@pytest.fixture(autouse=True)
def clear_llm_env(monkeypatch) -> None:
    for key in ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT_SECONDS"]:
        monkeypatch.delenv(key, raising=False)
