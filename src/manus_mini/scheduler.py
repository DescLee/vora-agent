from __future__ import annotations

from pathlib import Path

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
            batch_sensitive = False
            batch_sensitive_resources: set[str] = set()
            for call in ready:
                tool = self.registry.get(call.name)
                if self._is_sensitive(call, tool):
                    if batch:
                        continue
                    batch.append(call)
                    batch_sensitive = True
                    batch_sensitive_resources.update(self._resource_keys(call, tool))
                    continue

                if batch_sensitive:
                    continue

                if self._conflicts(call, batch_sensitive, batch_sensitive_resources):
                    continue

                batch.append(call)
                if batch_sensitive:
                    batch_sensitive_resources.update(self._resource_keys(call, tool))

            if not batch:
                call = ready[0]
                tool = self.registry.get(call.name)
                batch = [call]
                if self._is_sensitive(call, tool):
                    batch_sensitive = True
                    batch_sensitive_resources.update(self._resource_keys(call, tool))

            batches.append(batch)
            for call in batch:
                scheduled.add(call.id)
                remaining = [item for item in remaining if item.id != call.id]

        return batches

    def _is_sensitive(self, call: ToolCall, tool) -> bool:
        return call.risk_level != "safe" or not tool.is_read_only

    def _resource_keys(self, call: ToolCall, tool) -> set[str]:
        keys = call.resource_keys or tool.resource_keys(**call.args)
        return {Path(key).as_posix() for key in keys}

    def _conflicts(
        self,
        call: ToolCall,
        batch_sensitive: bool,
        batch_sensitive_resources: set[str],
    ) -> bool:
        if not batch_sensitive or not batch_sensitive_resources:
            return False
        keys = set(call.resource_keys)
        return bool(keys.intersection(batch_sensitive_resources))
