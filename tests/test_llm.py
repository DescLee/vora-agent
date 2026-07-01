import io
import urllib.error

import pytest

from manus_mini.config import AppConfig
from manus_mini.llm import LLMRequestError, OpenAICompatibleLLMClient, openai_messages, tool_schema
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

    assert "limit" in list_schema["properties"]
    assert "max_bytes" in read_schema["properties"]
    assert "max_bytes" in write_schema["properties"]
