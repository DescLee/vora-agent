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
    source_request: dict[str, Any] = Field(default_factory=dict)
    source_response: dict[str, Any] = Field(default_factory=dict)


class LLMClient:
    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> LLMResult:
        raise NotImplementedError

    def context_limit(self) -> int | None:
        return None


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


def extract_usage(payload: dict[str, Any]) -> dict[str, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    extracted: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            extracted[key] = value
    return extracted or None


def infer_model_context_limit(model_name: str) -> int | None:
    normalized = model_name.strip().lower()
    if not normalized:
        return None
    if normalized.startswith("deepseek"):
        return 1_000_000
    return None


def tool_schema(name: str) -> dict[str, Any]:
    from manus_mini.tools.registry import ToolRegistry

    try:
        return ToolRegistry().get(name).parameters_schema()
    except KeyError:
        return {"type": "object", "properties": {}, "additionalProperties": True}


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
            return self._parse_message(message, payload=payload, body=body, tool_names=tool_names)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMRequestError("LLM returned malformed response") from error

    def context_limit(self) -> int | None:
        return infer_model_context_limit(self.config.llm_model)

    def _extract_message(self, body: dict[str, Any]) -> dict[str, Any]:
        choices = body["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("missing choices")
        message = choices[0]["message"]
        if not isinstance(message, dict):
            raise ValueError("missing message")
        return message

    def _parse_message(
        self,
        message: dict[str, Any],
        payload: dict[str, Any],
        body: dict[str, Any],
        tool_names: list[str],
    ) -> LLMResult:
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
            source_request={"messages": payload["messages"], "tool_names": list(tool_names), "payload": payload},
            source_response=body,
        )


def get_default_llm_client(config: AppConfig | None = None) -> LLMClient:
    app_config = config or AppConfig.from_env()
    if app_config.llm_provider == "openai-compatible":
        return OpenAICompatibleLLMClient(app_config)
    raise RuntimeError("LLM_PROVIDER must be set to openai-compatible")
