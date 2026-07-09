import io
import urllib.error

import pytest

from manus_mini.config import AppConfig
from manus_mini.llm import LLMRequestError, OpenAICompatibleLLMClient, extract_usage, infer_model_context_limit, openai_messages, tool_schema
from manus_mini.tools import ToolRegistry
from manus_mini.models import Message


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


def test_tool_schema_is_loaded_from_registered_tool() -> None:
    registry_schema = ToolRegistry().get("replace_in_file").parameters_schema()

    assert tool_schema("replace_in_file") == registry_schema


def test_infer_model_context_limit_supports_deepseek_family() -> None:
    assert infer_model_context_limit("deepseek-v4-flash") == 1_000_000
    assert infer_model_context_limit("deepseek-v4-pro") == 1_000_000
    assert infer_model_context_limit("gpt-4o-mini") == 128_000
