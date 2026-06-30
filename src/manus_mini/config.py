from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    env_path = Path(path)
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)

    return loaded


@dataclass(slots=True)
class AppConfig:
    llm_provider: str = "mock"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 30

    @classmethod
    def from_env(cls, env_path: str | Path = ".env") -> "AppConfig":
        explicit_env = {
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
            "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
            "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
            "LLM_MODEL": os.environ.get("LLM_MODEL"),
            "LLM_TIMEOUT_SECONDS": os.environ.get("LLM_TIMEOUT_SECONDS"),
        }
        loaded_env = load_dotenv(env_path)

        def resolve(key: str, default: str) -> str:
            if explicit_env[key] is not None:
                return explicit_env[key] or default
            return loaded_env.get(key, default)

        timeout_value = resolve("LLM_TIMEOUT_SECONDS", "30")
        try:
            timeout_seconds = int(timeout_value)
        except ValueError:
            timeout_seconds = 30

        return cls(
            llm_provider=resolve("LLM_PROVIDER", "mock").strip().lower() or "mock",
            llm_base_url=resolve("LLM_BASE_URL", "").strip(),
            llm_api_key=resolve("LLM_API_KEY", "").strip(),
            llm_model=resolve("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            llm_timeout_seconds=max(1, timeout_seconds),
        )
