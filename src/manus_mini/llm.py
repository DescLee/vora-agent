from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from manus_mini.models import ToolCall


class LLMResult(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class MockLLMClient:
    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> LLMResult:
        user_text = " ".join(
            getattr(message, "content", "")
            for message in messages
            if getattr(message, "role", "") == "user"
        )
        tool_text = " ".join(
            getattr(message, "content", "")
            for message in messages
            if getattr(message, "role", "") == "tool"
        )

        if tool_text:
            return LLMResult(content=f"已根据工具结果生成草稿：{tool_text}")

        if "a.md" in user_text and "read_file" in tool_names:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-read-a",
                        name="read_file",
                        args={"path": "a.md"},
                    )
                ]
            )

        if "docs" in user_text and "list_files" in tool_names:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-list-docs",
                        name="list_files",
                        args={"path": "docs"},
                    )
                ]
            )

        return LLMResult(content=f"报告草稿：{user_text or '已生成'}")
