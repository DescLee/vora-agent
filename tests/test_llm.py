import io
import urllib.error

import pytest

from vora.config import AppConfig
from vora.llm import LLMRequestError, OpenAICompatibleLLMClient, compact_tool_schema, extract_usage, infer_model_context_limit, openai_messages, tool_schema
from vora.tools import ToolRegistry
from vora.models import Message


def test_openai_compatible_client_wraps_http_error(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url="http://localhost/v1/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(b'{"error":"bad tool schema"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    with pytest.raises(LLMRequestError, match="LLM HTTP 400"):
        client.complete_with_tools([Message.user("hi")], ["read_file"])


def test_openai_compatible_client_retries_transient_http_error(monkeypatch) -> None:
    calls = {"count": 0}
    delays = []

    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                url="http://localhost/v1/chat/completions",
                code=429,
                msg="Too Many Requests",
                hdrs={},
                fp=io.BytesIO(b'{"error":"rate limited"}'),
            )
        return io.BytesIO(b'{"choices":[{"message":{"content":"done","tool_calls":[]}}]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", delays.append)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    result = client.complete_with_tools([Message.user("hi")], [])

    assert calls["count"] == 2
    assert result.content == "done"
    assert len(delays) == 1
    assert 0.25 <= delays[0] <= 0.3


def test_openai_compatible_client_honors_retry_after(monkeypatch) -> None:
    calls = {"count": 0}
    delays = []

    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                url="http://localhost/v1/chat/completions",
                code=429,
                msg="Too Many Requests",
                hdrs={"Retry-After": "2"},
                fp=io.BytesIO(b'{"error":"rate limited"}'),
            )
        return io.BytesIO(b'{"choices":[{"message":{"content":"done","tool_calls":[]}}]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", delays.append)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    assert client.complete_with_tools([Message.user("hi")], []).content == "done"
    assert delays == [2.0]


def test_openai_compatible_client_wraps_malformed_success_response(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        return io.BytesIO(b'{"id":"chatcmpl-test","choices":[]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    with pytest.raises(LLMRequestError, match="malformed"):
        client.complete_with_tools([Message.user("hi")], ["read_file"])


def test_openai_compatible_client_rejects_tool_call_without_name(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        return io.BytesIO(
            b'{"choices":[{"message":{"tool_calls":[{"id":"call-1","type":"function","function":{"arguments":"{}"}}]}}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    with pytest.raises(LLMRequestError, match="malformed"):
        client.complete_with_tools([Message.user("hi")], ["read_file"])


def test_openai_compatible_client_rejects_non_object_tool_arguments(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        return io.BytesIO(
            b'{"choices":[{"message":{"tool_calls":[{"id":"call-1","type":"function","function":{"name":"read_file","arguments":"[]"}}]}}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    with pytest.raises(LLMRequestError, match="malformed"):
        client.complete_with_tools([Message.user("hi")], ["read_file"])


def test_openai_compatible_client_rejects_raw_tool_call_markup_in_content(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        return io.BytesIO(
            (
                '{"choices":[{"message":{"content":"<｜｜DSML｜｜tool_calls>\\n'
                '<｜｜DSML｜｜invoke name=\\"read_file\\">\\n'
                '<｜｜DSML｜｜parameter name=\\"path\\" string=\\"true\\">README.md</｜｜DSML｜｜parameter>\\n'
                '</｜｜DSML｜｜invoke>\\n'
                '</｜｜DSML｜｜tool_calls>"}}]}'
            ).encode("utf-8")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    with pytest.raises(LLMRequestError, match="tool call markup"):
        client.complete_with_tools([Message.user("hi")], ["read_file"])


def test_openai_compatible_client_exposes_source_request_and_response(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        return io.BytesIO(
            b'{"choices":[{"message":{"content":"done","tool_calls":[]}}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        )
    )

    result = client.complete_with_tools([Message.user("hi")], ["read_file"])

    assert result.source_request["messages"][0]["content"] == "hi"
    assert result.source_request["tool_names"] == ["read_file"]
    assert result.source_response["choices"][0]["message"]["content"] == "done"


def test_openai_compatible_client_keeps_usage_from_response(monkeypatch) -> None:
    def fake_urlopen(*args, **kwargs):  # noqa: ARG001
        return io.BytesIO(
            b'{"usage":{"prompt_tokens":123,"completion_tokens":45,"total_tokens":168},"choices":[{"message":{"content":"done","tool_calls":[]}}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost/v1",
            llm_api_key="test-key",
            llm_model="deepseek-v4-flash",
        )
    )

    result = client.complete_with_tools([Message.user("hi")], ["read_file"])

    assert result.source_response["usage"]["prompt_tokens"] == 123
    assert extract_usage(result.source_response) == {
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
    }


def test_extract_usage_keeps_cached_prompt_tokens() -> None:
    payload = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 70},
        }
    }

    assert extract_usage(payload) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cached_prompt_tokens": 70,
        "non_cached_prompt_tokens": 30,
    }


def test_compact_tool_schema_strips_descriptions_and_prunes_defs() -> None:
    schema = {
        "type": "object",
        "description": "large top-level description",
        "properties": {
            "path": {
                "type": "string",
                "description": "verbose field description",
            }
        },
        "$defs": {
            "Unused": {
                "type": "object",
                "description": "unreachable definition",
                "properties": {"value": {"type": "string"}},
            }
        },
        "required": ["path"],
    }

    compacted = compact_tool_schema(schema, max_bytes=5000)

    assert "description" not in compacted
    assert "description" not in compacted["properties"]["path"]
    assert "$defs" not in compacted
    assert compacted["required"] == ["path"]


def test_compact_tool_schema_collapses_large_deep_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "properties": {
                    f"field_{index}": {"type": "string", "description": "x" * 200}
                    for index in range(80)
                },
            }
        },
    }

    compacted = compact_tool_schema(schema, max_bytes=800, max_depth=2)

    encoded = str(compacted)
    assert len(encoded.encode("utf-8")) <= 800
    assert compacted["properties"]["payload"]["additionalProperties"] is True


def test_openai_messages_maps_agent_role_to_assistant() -> None:
    assistant_message = Message.agent("tool executed", tool_call_ids=["call-1"])
    assistant_message.metadata["tool_call_names"] = {"call-1": "read_file"}
    assistant_message.metadata["tool_call_arguments"] = {"call-1": '{"path":"README.md"}'}
    assistant_message.metadata["reasoning_content"] = "I should read the README."

    messages = openai_messages(
        [
            Message.user("hi"),
            assistant_message,
            Message.tool("result", tool_call_id="call-1"),
        ]
    )

    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call-1"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == '{"path":"README.md"}'
    assert messages[1]["reasoning_content"] == "I should read the README."
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call-1"


def test_tool_schema_requires_read_file_path() -> None:
    schema = tool_schema("read_file")

    assert schema["required"] == ["path"]
    assert "path" in schema["properties"]


def test_tool_schema_exposes_file_tool_limits() -> None:
    list_schema = tool_schema("list_files")
    read_schema = tool_schema("read_file")
    write_schema = tool_schema("write_file")
    replace_schema = tool_schema("replace_in_file")

    assert "limit" in list_schema["properties"]
    assert "max_bytes" in read_schema["properties"]
    assert "max_bytes" in write_schema["properties"]
    assert "allow_full_rewrite" in write_schema["properties"]
    assert replace_schema["required"] == ["path", "old_text", "new_text"]
    assert "expected_replacements" in replace_schema["properties"]
    assert "before_text" in replace_schema["properties"]
    assert "after_text" in replace_schema["properties"]


def test_tool_schema_exposes_shell_tools() -> None:
    bash_schema = tool_schema("run_bash")
    script_schema = tool_schema("run_temp_script")

    assert bash_schema["required"] == ["command"]
    assert "command" in bash_schema["properties"]
    assert script_schema["required"] == ["content"]
    assert "content" in script_schema["properties"]
    assert "is_test" in script_schema["properties"]
    assert "timeout_seconds" in script_schema["properties"]
    raw_script_schema = ToolRegistry().get("run_temp_script").parameters_schema()
    assert ".py" in raw_script_schema["properties"]["content"]["description"]
    assert ".cjs" in raw_script_schema["properties"]["content"]["description"]


def test_tool_schema_is_loaded_from_registered_tool() -> None:
    registry_schema = ToolRegistry().get("replace_in_file").parameters_schema()

    assert tool_schema("replace_in_file") == compact_tool_schema(registry_schema)


def test_infer_model_context_limit_supports_deepseek_family() -> None:
    assert infer_model_context_limit("deepseek-v4-flash") == 1_000_000
    assert infer_model_context_limit("deepseek-v4-pro") == 1_000_000
    assert infer_model_context_limit("gpt-4o-mini") == 128_000


def test_openai_compatible_client_context_limit_from_model_detail(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ARG001
        requested_urls.append(request.full_url)
        return io.BytesIO(b'{"id":"deepseek-v4-flash","context_window":65536}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost:1234/v1",
            llm_api_key="test-key",
            llm_model="deepseek-v4-flash",
        )
    )

    assert client.context_limit() == 65_536
    assert requested_urls == ["http://localhost:1234/v1/models/deepseek-v4-flash"]


def test_openai_compatible_client_context_limit_from_model_list(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ARG001
        requested_urls.append(request.full_url)
        if request.full_url.endswith("/models/deepseek-v4-flash"):
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=404,
                msg="Not Found",
                hdrs={},
                fp=io.BytesIO(b"{}"),
            )
        return io.BytesIO(
            b'{"data":[{"id":"other-model","context_window":8192},'
            b'{"id":"deepseek-v4-flash","max_model_len":64000}]}'
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = OpenAICompatibleLLMClient(
        AppConfig(
            llm_provider="openai-compatible",
            llm_base_url="http://localhost:1234/v1",
            llm_api_key="test-key",
            llm_model="deepseek-v4-flash",
        )
    )

    assert client.context_limit() == 64_000
    assert requested_urls == [
        "http://localhost:1234/v1/models/deepseek-v4-flash",
        "http://localhost:1234/v1/models",
    ]
