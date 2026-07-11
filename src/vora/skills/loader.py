from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from vora.skills.models import SkillSpec


def load_skills_from_roots(roots: Iterable[Path]) -> list[SkillSpec]:
    skills: list[SkillSpec] = []
    for root in roots:
        skills.extend(load_skills_from_root(root))
    return skills


def load_skills_from_root(root: Path) -> list[SkillSpec]:
    if not root.exists() or not root.is_dir():
        return []

    skills: list[SkillSpec] = []
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        skill = load_skill_from_dir(skill_dir)
        if skill is not None:
            skills.append(skill)
    return skills


def load_skill_from_dir(skill_dir: Path) -> SkillSpec | None:
    metadata_path = skill_dir / "skill.json"
    if not metadata_path.is_file():
        return None

    try:
        raw_metadata: dict[str, Any] = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    instructions = _read_optional_text(skill_dir / "instructions.md")
    if instructions and not raw_metadata.get("instructions"):
        raw_metadata["instructions"] = instructions

    try:
        return SkillSpec.model_validate(raw_metadata)
    except ValidationError:
        return None


def _read_optional_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
