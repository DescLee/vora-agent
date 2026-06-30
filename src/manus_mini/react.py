from __future__ import annotations

from manus_mini.context import validate_tool_call_pairs
from manus_mini.llm import LLMClient, get_default_llm_client
from manus_mini.models import Message, Observation, SessionState, TaskState
from manus_mini.scheduler import ToolScheduler
from manus_mini.tools.registry import ToolRegistry


class ReActLoop:
    def __init__(self, llm: LLMClient | None = None, registry: ToolRegistry | None = None) -> None:
        self.llm = llm or get_default_llm_client()
        self.registry = registry or ToolRegistry()
        self.scheduler = ToolScheduler(self.registry)

    def run(self, task: TaskState, session: SessionState) -> str:
        messages = [Message.user(task.goal)]

        for _ in range(task.limits.max_react_iterations):
            validate_tool_call_pairs(messages)
            llm_result = self.llm.complete_with_tools(messages, self.registry.names())

            if not llm_result.tool_calls:
                return llm_result.content

            batches = self.scheduler.plan(llm_result.tool_calls)
            for batch in batches:
                for call in batch:
                    tool = self.registry.get(call.name)
                    run_args = dict(call.args)
                    run_args["workspace"] = session.cwd
                    tool_result = tool.run(**run_args)
                    task.observations.append(
                        Observation(
                            tool_call_id=call.id,
                            ok=tool_result.ok,
                            summary=tool_result.summary,
                            content=tool_result.content,
                        )
                    )
                    messages.append(Message.agent("tool executed", tool_call_ids=[call.id]))
                    messages.append(Message.tool(tool_result.content or tool_result.summary, tool_call_id=call.id))

        raise RuntimeError("MAX_REACT_ITERATIONS_REACHED")
