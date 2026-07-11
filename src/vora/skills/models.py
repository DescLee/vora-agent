from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSpec(BaseModel):
    name: str
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    instructions: str = ""
    tool_allowlist: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
