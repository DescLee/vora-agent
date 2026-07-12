from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from vora.logging import default_vora_home
from vora.skills.loader import load_skill_from_dir, load_skills_from_root
from vora.skills.models import SkillSpec


PLUGIN_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class InstalledPlugin:
    name: str
    source: str
    path: Path
    skills: list[SkillSpec]


def plugins_root() -> Path:
    return default_vora_home() / "plugins"


def install_plugin(source: str, *, name: str | None = None) -> InstalledPlugin:
    if not _is_git_url(source):
        raise ValueError("plugin source must be a Git URL")
    with tempfile.TemporaryDirectory(prefix="vora-plugin-") as temp_dir:
        cloned = Path(temp_dir) / "repo"
        _clone_plugin(source, cloned)
        plugin_name = name or _infer_plugin_name(cloned, source)
        _validate_plugin_name(plugin_name)
        skills = discover_plugin_skills(cloned)
        if not skills:
            raise ValueError("plugin repository must contain at least one skill.json or SKILL.md")
        target = plugins_root() / plugin_name
        if target.exists():
            shutil.rmtree(target)
        (target / "repo").parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cloned, target / "repo")
        _write_plugin_manifest(target, name=plugin_name, source=source)
        return InstalledPlugin(name=plugin_name, source=source, path=target, skills=skills)


def remove_plugin(name: str) -> Path:
    _validate_plugin_name(name)
    target = plugins_root() / name
    if not target.is_dir():
        raise FileNotFoundError(target)
    shutil.rmtree(target)
    return target


def list_plugins() -> list[InstalledPlugin]:
    root = plugins_root()
    if not root.is_dir():
        return []
    plugins: list[InstalledPlugin] = []
    for plugin_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        repo = plugin_dir / "repo"
        if not repo.is_dir():
            continue
        source = _read_plugin_source(plugin_dir)
        plugins.append(
            InstalledPlugin(
                name=plugin_dir.name,
                source=source,
                path=plugin_dir,
                skills=discover_plugin_skills(repo),
            )
        )
    return plugins


def plugin_skill_roots() -> list[Path]:
    roots: list[Path] = []
    for plugin in list_plugins():
        repo = plugin.path / "repo"
        if load_skill_from_dir(repo) is not None:
            roots.append(repo.parent)
        skills_dir = repo / "skills"
        if skills_dir.is_dir():
            roots.append(skills_dir)
    return roots


def discover_plugin_skills(repo: Path) -> list[SkillSpec]:
    if load_skill_from_dir(repo) is not None:
        skill = load_skill_from_dir(repo)
        return [skill] if skill is not None else []
    skills_dir = repo / "skills"
    if skills_dir.is_dir():
        return load_skills_from_root(skills_dir)
    return []


def _clone_plugin(source: str, target: Path) -> None:
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", source, str(target)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise ValueError("git is required to install plugins") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        message = f"failed to clone plugin repository: {source}"
        if detail:
            message += f"\n{detail}"
        raise ValueError(message) from error


def _infer_plugin_name(repo: Path, source: str) -> str:
    manifest_name = _read_manifest_name(repo / ".codex-plugin" / "plugin.json")
    if manifest_name:
        return manifest_name
    parsed = urlparse(source)
    candidate = Path(parsed.path).name or "plugin"
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    return candidate


def _read_manifest_name(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    name = payload.get("name")
    return name.strip() if isinstance(name, str) else ""


def _write_plugin_manifest(path: Path, *, name: str, source: str) -> None:
    payload = {"name": name, "source": source}
    (path / "vora-plugin.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_plugin_source(path: Path) -> str:
    manifest = path / "vora-plugin.json"
    if not manifest.is_file():
        return ""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    source = payload.get("source")
    return source if isinstance(source, str) else ""


def _is_git_url(value: str) -> bool:
    if value.startswith("git@"):
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https", "ssh", "git"} and bool(parsed.netloc)


def _validate_plugin_name(name: str) -> None:
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid plugin name: {name}")
