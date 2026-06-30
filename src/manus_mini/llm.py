from __future__ import annotations

import json
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from manus_mini.config import AppConfig
from manus_mini.models import ToolCall


class LLMResult(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class LLMClient:
    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> LLMResult:
        raise NotImplementedError


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


class OpenAICompatibleLLMClient(LLMClient):
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> LLMResult:
        if not self.config.llm_base_url:
            raise RuntimeError("LLM_BASE_URL is not configured")
        if not self.config.llm_api_key:
            raise RuntimeError("LLM_API_KEY is not configured")

        payload = {
            "model": self.config.llm_model,
            "messages": [
                {"role": getattr(message, "role", "user"), "content": getattr(message, "content", "")}
                for message in messages
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Tool {name}",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
                    },
                }
                for name in tool_names
            ],
            "tool_choice": "auto",
        }
        request = urllib.request.Request(
            url=self.config.llm_base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.llm_api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.llm_timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))

        message = body["choices"][0]["message"]
        tool_calls: list[ToolCall] = []
        for item in message.get("tool_calls", []):
            arguments = item.get("function", {}).get("arguments", "{}")
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                parsed_arguments = {}
            tool_calls.append(
                ToolCall(
                    id=item.get("id", ""),
                    name=item.get("function", {}).get("name", ""),
                    args=parsed_arguments,
                )
            )

        return LLMResult(content=message.get("content") or "", tool_calls=tool_calls)


def get_default_llm_client(config: AppConfig | None = None) -> LLMClient:
    app_config = config or AppConfig.from_env()
    if app_config.llm_provider == "openai-compatible":
        return OpenAICompatibleLLMClient(app_config)
    return MockLLMClient()
