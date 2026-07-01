from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from manus_mini.config import AppConfig
from manus_mini.models import ToolCall


class LLMResult(BaseModel):
    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_arguments: dict[str, str] = Field(default_factory=dict)


class LLMClient:
    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> LLMResult:
        raise NotImplementedError


class LLMRequestError(RuntimeError):
    pass


def openai_role(role: str) -> str:
    if role == "agent":
        return "assistant"
    return role


def openai_messages(messages: list[Any]) -> list[dict[str, Any]]:
    payload_messages: list[dict[str, Any]] = []
    for message in messages:
        role = openai_role(getattr(message, "role", "user"))
        payload_message: dict[str, Any] = {
            "role": role,
            "content": getattr(message, "content", ""),
        }
        tool_call_id = getattr(message, "tool_call_id", None)
        if role == "tool" and tool_call_id:
            payload_message["tool_call_id"] = tool_call_id

        tool_call_ids = list(getattr(message, "tool_call_ids", []) or [])
        if role == "assistant" and tool_call_ids:
            tool_call_names = dict(getattr(message, "metadata", {}).get("tool_call_names", {}))
            tool_call_arguments = dict(getattr(message, "metadata", {}).get("tool_call_arguments", {}))
            payload_message["tool_calls"] = [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call_names.get(tool_call_id, "unknown_tool"),
                        "arguments": tool_call_arguments.get(tool_call_id, "{}"),
                    },
                }
                for tool_call_id in tool_call_ids
            ]
        reasoning_content = getattr(message, "metadata", {}).get("reasoning_content")
        if role == "assistant" and reasoning_content:
            payload_message["reasoning_content"] = reasoning_content
        payload_messages.append(payload_message)
    return payload_messages


def tool_schema(name: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "list_files": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory or file path inside the workspace. Use '.' for project root.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return.",
                    "default": 500,
                    "minimum": 1,
                    "maximum": 2000,
                },
            },
            "additionalProperties": False,
        },
        "read_file": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path inside the workspace, for example README.md.",
                },
                "encoding": {"type": "string", "default": "utf-8"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read.",
                    "default": 1000000,
                    "minimum": 1,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "write_file": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative output file path inside the workspace."},
                "content": {"type": "string", "description": "File content to write."},
                "encoding": {"type": "string", "default": "utf-8"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes allowed for the written content.",
                    "default": 1000000,
                    "minimum": 1,
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    }
    return schemas.get(name, {"type": "object", "properties": {}, "additionalProperties": True})


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

        project_query = any(keyword in user_text for keyword in ["当前项目", "这个项目", "项目是做什么", "项目作用"])
        create_hello_world_query = (
            "helloworld.py" in user_text
            and any(keyword in user_text for keyword in ["新建", "创建", "生成", "写一个"])
            and "write_file" in tool_names
        )
        if create_hello_world_query and "wrote helloworld.py" not in tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-write-helloworld",
                        name="write_file",
                        args={
                            "path": "helloworld.py",
                            "content": "print('hello world')\n",
                        },
                    )
                ]
            )

        if create_hello_world_query and "wrote helloworld.py" in tool_text:
            return LLMResult(content="已在工作目录下新建 helloworld.py，内容为 `print('hello world')`。")

        if project_query and "list_files" in tool_names and not tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-list-project",
                        name="list_files",
                        args={"path": "."},
                    )
                ]
            )

        if project_query and "read_file" in tool_names and "paths:" in tool_text and "# manus-mini" not in tool_text:
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-read-readme",
                        name="read_file",
                        args={"path": "README.md"},
                    ),
                    ToolCall(
                        id="call-read-pyproject",
                        name="read_file",
                        args={"path": "pyproject.toml"},
                    ),
                    ToolCall(
                        id="call-read-technical-design",
                        name="read_file",
                        args={"path": "docs/v1-technical-design.md"},
                    ),
                ]
            )

        if project_query and "# manus-mini" in tool_text:
            return LLMResult(
                content=(
                    "这个项目是 manus-mini，一个面向面试展示的 TUI 版 Manus/Agent 原型。"
                    "它的核心作用是让用户在终端里连续对话，驱动 Agent 通过 ReAct 循环调用文件工具，"
                    "再经过 reflection 与外层工程循环生成调研、总结或代码相关产物。"
                    "项目已经包含 prompt_toolkit TUI、OpenAI-compatible/Mock LLM 配置、文件工具、"
                    "工具调度、长期记忆、上下文压缩基础、运行日志、产物输出和过程 trace 展示。"
                )
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
            "messages": openai_messages(messages),
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Tool {name}",
                        "parameters": tool_schema(name),
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
        try:
            with urllib.request.urlopen(request, timeout=self.config.llm_timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            detail = body[:800] if body else error.reason
            raise LLMRequestError(f"LLM HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise LLMRequestError(f"LLM request failed: {error.reason}") from error
        except TimeoutError as error:
            raise LLMRequestError("LLM request timed out") from error
        except json.JSONDecodeError as error:
            raise LLMRequestError("LLM returned invalid JSON") from error

        try:
            message = self._extract_message(body)
            return self._parse_message(message)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMRequestError("LLM returned malformed response") from error

    def _extract_message(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("missing choices")
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise ValueError("missing message")
        return message

    def _parse_message(self, message: dict[str, Any]) -> LLMResult:
        tool_calls: list[ToolCall] = []
        tool_call_arguments: dict[str, str] = {}
        raw_tool_calls = message.get("tool_calls") or []
        if not isinstance(raw_tool_calls, list):
            raise ValueError("tool_calls must be a list")

        for index, item in enumerate(raw_tool_calls):
            if not isinstance(item, dict):
                raise ValueError("tool call must be an object")
            function = item.get("function") or {}
            if not isinstance(function, dict):
                raise ValueError("tool call function must be an object")
            tool_name = function.get("name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("tool call function name is required")
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            tool_call_id = str(item.get("id") or f"call-{index}")
            tool_call_arguments[tool_call_id] = arguments
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError as error:
                raise ValueError("tool call arguments must be valid JSON") from error
            if not isinstance(parsed_arguments, dict):
                raise ValueError("tool call arguments must be an object")
            tool_calls.append(
                ToolCall(
                    id=tool_call_id,
                    name=tool_name.strip(),
                    args=parsed_arguments,
                )
            )

        return LLMResult(
            content=message.get("content") or "",
            reasoning_content=message.get("reasoning_content") or "",
            tool_calls=tool_calls,
            tool_call_arguments=tool_call_arguments,
        )


def get_default_llm_client(config: AppConfig | None = None) -> LLMClient:
    app_config = config or AppConfig.from_env()
    if app_config.llm_provider == "openai-compatible":
        return OpenAICompatibleLLMClient(app_config)
    return MockLLMClient()
