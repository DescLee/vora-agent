from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from vora.logging import default_vora_home, project_storage_dir


MCP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class MCPServerConfig(BaseModel):
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class MCPConfig(BaseModel):
    servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


def mcp_config_path(cwd: Path, *, global_scope: bool = False) -> Path:
    root = default_vora_home() if global_scope else project_storage_dir(cwd)
    return root / "mcp.json"


def load_mcp_config(path: Path) -> MCPConfig:
    if not path.is_file():
        return MCPConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return MCPConfig.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError):
        return MCPConfig()


def save_mcp_config(path: Path, config: MCPConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_mcp_server(
    cwd: Path,
    name: str,
    *,
    command: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    global_scope: bool = False,
) -> Path:
    _validate_mcp_name(name)
    if not command.strip():
        raise ValueError("command is required")
    path = mcp_config_path(cwd, global_scope=global_scope)
    config = load_mcp_config(path)
    config.servers[name] = MCPServerConfig(command=command, args=list(args or []), env=dict(env or {}))
    save_mcp_config(path, config)
    return path


def remove_mcp_server(cwd: Path, name: str, *, global_scope: bool = False) -> Path:
    _validate_mcp_name(name)
    path = mcp_config_path(cwd, global_scope=global_scope)
    config = load_mcp_config(path)
    if name not in config.servers:
        raise KeyError(name)
    del config.servers[name]
    save_mcp_config(path, config)
    return path


def parse_env_pairs(pairs: list[str] | None) -> dict[str, str]:
    env: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"invalid env pair: {pair}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"invalid env pair: {pair}")
        env[key] = value
    return env


def format_mcp_command(server: MCPServerConfig) -> str:
    return " ".join([server.command, *server.args]).strip()


def _validate_mcp_name(name: str) -> None:
    if not MCP_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid MCP server name: {name}")
