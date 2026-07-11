from __future__ import annotations

from vora.models import Observation, ToolCall
from vora.tools.base import ToolResult


class Observer:
    def observe(self, call: ToolCall, result: ToolResult) -> Observation:
        return Observation(
            tool_call_id=call.id,
            ok=result.ok,
            summary=result.summary,
            content=result.content,
        )
