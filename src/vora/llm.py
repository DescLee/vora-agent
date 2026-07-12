from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from vora.config import AppConfig
from vora.models import ToolCall

RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 30.0
MAX_COMPACT_TOOL_SCHEMA_BYTES = 5_000
MAX_COMPACT_TOOL_SCHEMA_DEPTH = 3


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


_RAW_TOOL_CALL_MARKUP_SNIPPETS = (
    "<｜｜DSML｜｜tool_calls>",
    "</｜｜DSML｜｜tool_calls>",
    "<｜｜DSML｜｜invoke name=",
)


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
    cached_prompt_tokens = _extract_cached_prompt_tokens(usage)
    if cached_prompt_tokens is not None:
        prompt_tokens = extracted.get("prompt_tokens", 0)
        cached_prompt_tokens = min(cached_prompt_tokens, prompt_tokens) if prompt_tokens > 0 else cached_prompt_tokens
        extracted["cached_prompt_tokens"] = cached_prompt_tokens
        if prompt_tokens > 0:
            extracted["non_cached_prompt_tokens"] = max(0, prompt_tokens - cached_prompt_tokens)
    return extracted or None


def _extract_cached_prompt_tokens(usage: dict[str, Any]) -> int | None:
    for detail_key in ("prompt_tokens_details", "input_tokens_details", "input_token_details"):
        details = usage.get(detail_key)
        if not isinstance(details, dict):
            continue
        for cached_key in ("cached_tokens", "cached_input_tokens", "cache_read_input_tokens"):
            value = details.get(cached_key)
            if isinstance(value, int) and value >= 0:
                return value
    for key in ("cached_prompt_tokens", "cached_input_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None


# 已知模型的上下文窗口映射（前缀匹配，更具体的排前面）
_KNOWN_MODEL_CONTEXT_LIMITS: list[tuple[str, int]] = [
    # DeepSeek 系列
    ("deepseek-r1", 1_000_000),
    ("deepseek-v4", 1_000_000),
    ("deepseek-v3", 1_000_000),
    ("deepseek-chat", 1_000_000),
    # GPT-4o 系列（128K 上下文），必须在 gpt-4 之前匹配
    ("gpt-4o-mini", 128_000),
    ("gpt-4o-", 128_000),
    ("gpt-4o", 128_000),
    # GPT-4 系列
    ("gpt-4-turbo", 128_000),
    ("gpt-4-1106", 128_000),
    ("gpt-4-0125", 128_000),
    ("gpt-4-32k", 32_768),
    ("gpt-4", 8_192),
    # GPT-3.5 系列
    ("gpt-3.5-turbo-1106", 16_384),
    ("gpt-3.5-turbo-0125", 16_384),
    ("gpt-3.5-turbo", 16_384),
    # Claude 系列
    ("claude-3-5-sonnet", 200_000),
    ("claude-3-5-haiku", 200_000),
    ("claude-3-opus", 200_000),
    ("claude-3-sonnet", 200_000),
    ("claude-3-haiku", 200_000),
    ("claude", 100_000),
    # Gemini 系列
    ("gemini-1.5-pro", 2_000_000),
    ("gemini-1.5-flash", 1_000_000),
    ("gemini-2.0-flash", 1_000_000),
    ("gemini", 32_768),
    # Llama 系列
    ("llama3.1-405b", 131_072),
    ("llama3.1-70b", 131_072),
    ("llama3.1-8b", 131_072),
    ("llama3-70b", 8_192),
    ("llama3-8b", 8_192),
    ("llama-2-70b", 4_096),
    ("codellama", 16_384),
    # 其他常见开源模型
    ("mixtral", 32_768),
    ("mistral-7b", 32_768),
    ("qwen2", 131_072),
    ("qwen1.5", 32_768),
    ("phi-3-mini", 128_000),
    ("phi-3-small", 128_000),
    ("phi-3-medium", 128_000),
    ("phi-3", 128_000),
]


def infer_model_context_limit(model_name: str) -> int | None:
    """通过模型名推断上下文窗口大小（静态兜底方案）"""
    normalized = model_name.strip().lower()
    if not normalized:
        return None
    for prefix, limit in _KNOWN_MODEL_CONTEXT_LIMITS:
        if normalized.startswith(prefix):
            return limit
    return None


def tool_schema(name: str) -> dict[str, Any]:
    from vora.tools.registry import ToolRegistry

    try:
        return compact_tool_schema(ToolRegistry().get(name).parameters_schema())
    except KeyError:
        return {"type": "object", "properties": {}, "additionalProperties": True}


def compact_tool_schema(
    schema: dict[str, Any],
    *,
    max_bytes: int = MAX_COMPACT_TOOL_SCHEMA_BYTES,
    max_depth: int = MAX_COMPACT_TOOL_SCHEMA_DEPTH,
) -> dict[str, Any]:
    compacted = _strip_schema_noise(schema)
    compacted = _prune_unreachable_definitions(compacted)
    if _json_size(compacted) <= max_bytes:
        return compacted
    compacted = _collapse_deep_schema_objects(compacted, max_depth=max_depth)
    if _json_size(compacted) <= max_bytes:
        return compacted
    compacted = _drop_definition_blocks(compacted)
    if _json_size(compacted) <= max_bytes:
        return compacted
    return _fit_schema_to_byte_budget(compacted, max_bytes=max_bytes)


def _strip_schema_noise(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_schema_noise(item)
            for key, item in value.items()
            if key not in {"description", "examples", "example", "title", "$comment"}
        }
    if isinstance(value, list):
        return [_strip_schema_noise(item) for item in value]
    return value


def _prune_unreachable_definitions(schema: dict[str, Any]) -> dict[str, Any]:
    refs = _collect_schema_refs(schema)
    if not refs:
        return _drop_definition_blocks(schema)
    compacted = dict(schema)
    for defs_key in ("$defs", "definitions"):
        definitions = compacted.get(defs_key)
        if not isinstance(definitions, dict):
            continue
        retained = {name: value for name, value in definitions.items() if name in refs}
        if retained:
            compacted[defs_key] = retained
        else:
            compacted.pop(defs_key, None)
    return compacted


def _collect_schema_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.add(ref.rsplit("/", 1)[-1])
        for key, item in value.items():
            if key in {"$defs", "definitions"}:
                continue
            refs.update(_collect_schema_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_schema_refs(item))
    return refs


def _collapse_deep_schema_objects(value: Any, *, max_depth: int, depth: int = 0) -> Any:
    if isinstance(value, list):
        return [_collapse_deep_schema_objects(item, max_depth=max_depth, depth=depth) for item in value]
    if not isinstance(value, dict):
        return value
    if depth >= max_depth and isinstance(value.get("properties"), dict):
        collapsed: dict[str, Any] = {"type": value.get("type", "object"), "additionalProperties": True}
        if "required" in value:
            collapsed["required"] = value["required"]
        return collapsed
    return {
        key: _collapse_deep_schema_objects(item, max_depth=max_depth, depth=depth + 1)
        for key, item in value.items()
    }


def _drop_definition_blocks(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_definition_blocks(item)
            for key, item in value.items()
            if key not in {"$defs", "definitions"}
        }
    if isinstance(value, list):
        return [_drop_definition_blocks(item) for item in value]
    return value


def _fit_schema_to_byte_budget(schema: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    fitted = dict(schema)
    properties = fitted.get("properties")
    if not isinstance(properties, dict):
        return fitted
    retained: dict[str, Any] = {}
    required = set(fitted.get("required") or [])
    for name, value in properties.items():
        candidate = dict(fitted)
        candidate_properties = {**retained, name: value}
        candidate["properties"] = candidate_properties
        if _json_size(candidate) > max_bytes and name not in required:
            continue
        retained[name] = value
    fitted["properties"] = retained
    omitted = len(properties) - len(retained)
    if omitted > 0:
        fitted["x-vora-schema-omitted-properties"] = omitted
    return fitted


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


# API 响应中可能包含上下文窗口信息的常见字段名
_MODEL_CONTEXT_LIMIT_FIELDS = (
    "max_context_tokens",
    "context_length",
    "max_context_length",
    "max_model_len",
    "context_window",
    "max_position_embeddings",
    "input_token_limit",
    "max_input_tokens",
    "num_ctx",
    "max_tokens",
)

MODEL_CONTEXT_LOOKUP_TIMEOUT_SECONDS = 3


def _extract_model_context_limit(payload: dict[str, Any] | None, model_name: str) -> int | None:
    if payload is None:
        return None

    candidates: list[dict[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, list):
        normalized_model = model_name.strip().lower()
        for item in data:
            if not isinstance(item, dict):
                continue
            item_names = [
                str(item.get(field, "")).strip().lower()
                for field in ("id", "name", "model")
                if item.get(field) is not None
            ]
            if normalized_model in item_names:
                candidates.append(item)
    elif isinstance(data, dict):
        candidates.append(data)
    candidates.append(payload)

    for candidate in candidates:
        for field in _MODEL_CONTEXT_LIMIT_FIELDS:
            value = candidate.get(field) or candidate.get(f"model_{field}")
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
    return None


class OpenAICompatibleLLMClient(LLMClient):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cached_context_limit: int | None = None
        self._context_limit_fetched = False

    def _fetch_model_context_limit_from_api(self) -> int | None:
        """尝试从模型信息端点获取模型的上下文窗口大小"""
        base_url = self.config.llm_base_url.rstrip("/")
        model = self.config.llm_model

        detail = self._fetch_model_metadata(f"{base_url}/models/{model}")
        result = _extract_model_context_limit(detail, model)
        if result is not None:
            return result

        listing = self._fetch_model_metadata(f"{base_url}/models")
        return _extract_model_context_limit(listing, model)

    def _fetch_model_metadata(self, url: str) -> dict[str, Any] | None:
        try:
            request = urllib.request.Request(
                url=url,
                method="GET",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config.llm_api_key}",
                },
            )
            with urllib.request.urlopen(request, timeout=MODEL_CONTEXT_LOOKUP_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

        return body if isinstance(body, dict) else None

    def context_limit(self) -> int | None:
        """返回模型的上下文窗口大小（动态获取 + 模型名推断兜底）"""
        if self._context_limit_fetched:
            return self._cached_context_limit

        # 第一优先级：从 API 动态获取
        result = self._fetch_model_context_limit_from_api()

        # 第二优先级：通过模型名推断
        if result is None:
            result = infer_model_context_limit(self.config.llm_model)

        self._cached_context_limit = result
        self._context_limit_fetched = True
        return result

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
        body = self._send_request_with_retries(request)

        try:
            message = self._extract_message(body)
            return self._parse_message(message, payload=payload, body=body, tool_names=tool_names)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise LLMRequestError("LLM returned malformed response") from error

    def _send_request_with_retries(self, request: urllib.request.Request) -> dict[str, Any]:
        last_error: Exception | None = None
        max_attempts = self.config.llm_max_attempts
        for attempt in range(1, max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.llm_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code not in RETRYABLE_HTTP_STATUS_CODES or attempt == max_attempts:
                    body = error.read().decode("utf-8", errors="replace")
                    detail = body[:800] if body else error.reason
                    raise LLMRequestError(f"LLM HTTP {error.code}: {detail}") from error
                last_error = error
                self._wait_before_retry(attempt, error.headers.get("Retry-After"))
            except urllib.error.URLError as error:
                if attempt == max_attempts:
                    raise LLMRequestError(f"LLM request failed: {error.reason}") from error
                last_error = error
                self._wait_before_retry(attempt)
            except TimeoutError as error:
                if attempt == max_attempts:
                    raise LLMRequestError("LLM request timed out") from error
                last_error = error
                self._wait_before_retry(attempt)
            except json.JSONDecodeError as error:
                raise LLMRequestError("LLM returned invalid JSON") from error
        raise LLMRequestError(f"LLM request failed: {last_error or 'unknown error'}")

    def _wait_before_retry(self, attempt: int, retry_after: str | None = None) -> None:
        delay = _retry_after_seconds(retry_after)
        if delay is None:
            base = self.config.llm_retry_backoff_seconds * (2 ** (attempt - 1))
            delay = base + random.uniform(0, base * 0.2)
        if delay > 0:
            time.sleep(min(delay, MAX_RETRY_AFTER_SECONDS))

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
        content = message.get("content") or ""
        if _looks_like_raw_tool_call_markup(content):
            raise LLMRequestError("LLM emitted raw tool call markup in content")

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
            content=content,
            reasoning_content=message.get("reasoning_content") or "",
            tool_calls=tool_calls,
            tool_call_arguments=tool_call_arguments,
            source_request={"messages": payload["messages"], "tool_names": list(tool_names), "payload": payload},
            source_response=body,
        )


def _looks_like_raw_tool_call_markup(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return False
    return any(snippet in stripped for snippet in _RAW_TOOL_CALL_MARKUP_SNIPPETS)


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, parsed)


def get_default_llm_client(config: AppConfig | None = None) -> LLMClient:
    app_config = config or AppConfig.from_env()
    if app_config.llm_provider == "openai-compatible":
        return OpenAICompatibleLLMClient(app_config)
    raise RuntimeError("LLM_PROVIDER must be set to openai-compatible")
