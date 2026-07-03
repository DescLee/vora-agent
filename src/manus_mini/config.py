from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LLM_TIMEOUT_SECONDS = 120


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    env_path = Path(path)
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = _parse_dotenv_value(value)
        if not key:
            continue
        loaded[key] = value
        os.environ.setdefault(key, value)

    return loaded


def _parse_dotenv_value(raw_value: str) -> str:
    value = _strip_inline_comment(raw_value.strip()).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


@dataclass(slots=True)
class AppConfig:
    llm_provider: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS

    @classmethod
    def from_env(
        cls,
        env_path: str | Path = ".env",
        user_env_path: str | Path | None = None,
        package_env_path: str | Path | None = None,
    ) -> "AppConfig":
        explicit_env = {
            "LLM_PROVIDER": os.environ.get("LLM_PROVIDER"),
            "LLM_BASE_URL": os.environ.get("LLM_BASE_URL"),
            "LLM_API_KEY": os.environ.get("LLM_API_KEY"),
            "LLM_MODEL": os.environ.get("LLM_MODEL"),
            "LLM_TIMEOUT_SECONDS": os.environ.get("LLM_TIMEOUT_SECONDS"),
        }
        loaded_env = load_dotenv(env_path)
        loaded_user_env = load_dotenv(user_env_path or Path.home() / ".manus-mini" / ".env")
        loaded_package_env = load_dotenv(package_env_path or _default_package_env_path())

        def resolve(key: str, default: str) -> str:
            if explicit_env[key] is not None:
                return explicit_env[key] or default
            return loaded_env.get(key, loaded_user_env.get(key, loaded_package_env.get(key, default)))

        default_timeout = str(DEFAULT_LLM_TIMEOUT_SECONDS)
        timeout_value = resolve("LLM_TIMEOUT_SECONDS", default_timeout)
        try:
            timeout_seconds = int(timeout_value)
        except ValueError:
            timeout_seconds = DEFAULT_LLM_TIMEOUT_SECONDS

        return cls(
            llm_provider=resolve("LLM_PROVIDER", "").strip().lower(),
            llm_base_url=resolve("LLM_BASE_URL", "").strip(),
            llm_api_key=resolve("LLM_API_KEY", "").strip(),
            llm_model=resolve("LLM_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            llm_timeout_seconds=max(1, timeout_seconds),
        )


def _default_package_env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"
