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
    if metadata_path.is_file():
        try:
            loaded_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(loaded_metadata, dict):
            return None
        raw_metadata: dict[str, Any] = loaded_metadata
    else:
        skill_md_metadata = _load_skill_md_metadata(skill_dir / "SKILL.md")
        if skill_md_metadata is None:
            return None
        raw_metadata = skill_md_metadata

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


def _load_skill_md_metadata(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    metadata, body = _parse_skill_md(raw)
    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    raw_description = metadata.get("description")
    description = raw_description if isinstance(raw_description, str) else ""
    triggers = metadata.get("triggers") if isinstance(metadata.get("triggers"), list) else []
    if not triggers:
        triggers = [name.strip()]
        if description:
            triggers.append(description)
    return {
        "name": name.strip(),
        "description": description.strip(),
        "triggers": triggers,
        "instructions": body.strip(),
    }


def _parse_skill_md(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    metadata_lines: list[str] = []
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
        metadata_lines.append(line)
    if end_index is None:
        return {}, raw
    metadata: dict[str, Any] = {}
    for line in metadata_lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"').strip("'")
    return metadata, "\n".join(lines[end_index + 1 :]).strip()
