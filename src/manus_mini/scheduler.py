from __future__ import annotations

from manus_mini.models import ToolCall
from manus_mini.tools.registry import ToolRegistry


class ToolScheduler:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def plan(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        remaining = list(tool_calls)
        scheduled: set[str] = set()
        batches: list[list[ToolCall]] = []

        while remaining:
            ready = [
                call
                for call in remaining
                if all(dependency in scheduled for dependency in call.depends_on)
            ]
            if not ready:
                raise ValueError("Tool dependency cycle detected")

            batch: list[ToolCall] = []
            for call in ready:
                if call.risk_level != "safe":
                    continue
                if self._conflicts(call, batch):
                    continue
                batch.append(call)

            if not batch:
                batch = [ready[0]]

            batches.append(batch)
            for call in batch:
                scheduled.add(call.id)
                remaining = [item for item in remaining if item.id != call.id]

        return batches

    def _conflicts(self, call: ToolCall, batch: list[ToolCall]) -> bool:
        keys = set(call.resource_keys)
        return any(keys.intersection(other.resource_keys) for other in batch)
