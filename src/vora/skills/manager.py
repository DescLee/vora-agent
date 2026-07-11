from __future__ import annotations

import re
import shutil
from pathlib import Path

from vora.logging import default_vora_home
from vora.skills.loader import load_skill_from_dir
from vora.skills.models import SkillSpec
from vora.skills.registry import BUILTIN_SKILLS


SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def skills_root(cwd: Path, *, global_scope: bool = False) -> Path:
    return (default_vora_home() / "skills") if global_scope else (cwd / "skills")


def add_skill(cwd: Path, source_dir: Path, *, name: str | None = None, global_scope: bool = False) -> tuple[SkillSpec, Path]:
    skill = load_skill_from_dir(source_dir)
    if skill is None:
        raise ValueError("source directory must contain a valid skill.json")
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
