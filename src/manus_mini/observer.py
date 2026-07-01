from __future__ import annotations

from manus_mini.models import Observation, ToolCall
from manus_mini.tools.base import ToolResult


class Observer:
    def observe(self, call: ToolCall, result: ToolResult) -> Observation:
        return Observation(
            tool_call_id=call.id,
            ok=result.ok,
            summary=result.summary,
            content=result.content,
        )
