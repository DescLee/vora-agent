from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from vora.logging import default_vora_home
from vora.skills.loader import load_skill_from_dir
from vora.skills.models import SkillSpec
from vora.skills.registry import BUILTIN_SKILLS


SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def skills_root(cwd: Path, *, global_scope: bool = False) -> Path:
    return (default_vora_home() / "skills") if global_scope else (cwd / "skills")


def add_skill(cwd: Path, source_dir: str | Path, *, name: str | None = None, global_scope: bool = False) -> tuple[SkillSpec, Path]:
    if _is_git_url(source_dir):
        with tempfile.TemporaryDirectory(prefix="vora-skill-") as temp_dir:
            cloned = Path(temp_dir) / "repo"
            _clone_git_skill(str(source_dir), cloned)
            skill_dir = _resolve_skill_source_dir(cloned)
            return _copy_skill(cwd, skill_dir, name=name, global_scope=global_scope)
    return _copy_skill(cwd, Path(source_dir), name=name, global_scope=global_scope)


def _copy_skill(cwd: Path, source_dir: Path, *, name: str | None = None, global_scope: bool = False) -> tuple[SkillSpec, Path]:
    skill = load_skill_from_dir(source_dir)
    if skill is None:
        raise ValueError("source directory must contain a valid skill.json or SKILL.md")
    target_name = name or skill.name
    _validate_skill_name(target_name)
    target = skills_root(cwd, global_scope=global_scope) / target_name
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target)
    return skill.model_copy(update={"name": target_name}), target


def remove_skill(cwd: Path, name: str, *, global_scope: bool = False) -> Path:
    _validate_skill_name(name)
    target = skills_root(cwd, global_scope=global_scope) / name
    if not target.is_dir():
        if any(skill.name == name for skill in BUILTIN_SKILLS):
            raise ValueError(f"cannot remove built-in skill: {name}")
        raise FileNotFoundError(target)
    shutil.rmtree(target)
    return target


def _validate_skill_name(name: str) -> None:
    if not SKILL_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid skill name: {name}")


def _is_git_url(value: str | Path) -> bool:
    text = str(value)
    if text.startswith("git@"):
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https", "ssh", "git"} and bool(parsed.netloc)


def _clone_git_skill(url: str, target: Path) -> None:
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise ValueError("git is required to install Skills from URLs") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        message = f"failed to clone Skill repository: {url}"
        if detail:
            message += f"\n{detail}"
        raise ValueError(message) from error


def _resolve_skill_source_dir(root: Path) -> Path:
    if load_skill_from_dir(root) is not None:
        return root
    candidates = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_dir() and load_skill_from_dir(path) is not None
    ]
    if not candidates:
        raise ValueError("cloned repository must contain a valid skill.json or SKILL.md")
    if len(candidates) > 1:
        names = ", ".join(path.relative_to(root).as_posix() for path in candidates[:8])
        raise ValueError(f"cloned repository contains multiple Skills; clone it manually and add one directory: {names}")
    return candidates[0]
