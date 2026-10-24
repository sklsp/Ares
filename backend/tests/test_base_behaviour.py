"""Behavioural tests for ``app.llm.base`` (provider-agnostic LLM types).

The provider suites exercise these types only incidentally; this file pins
the public contract directly: serialization round-trips, the tool-result
encoding rules, argument parsing error branches and the ``wants_tools``
flag. Every assertion uses literal expected values; no network is involved.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    ProviderStatus,
    ToolCall,
    parse_arguments,
)

# --- error hierarchy ------------------------------------------------------


def test_error_classes_form_the_documented_hierarchy():
    assert issubclass(LLMUnavailableError, LLMError) is True
    assert issubclass(LLMResponseError, LLMError) is True
    assert issubclass(LLMError, RuntimeError) is True


# --- LLMResponse / wants_tools --------------------------------------------


def test_response_defaults_to_empty_content_and_no_tool_calls():
    response = LLMResponse()
    assert response.content == ""
    assert response.tool_calls == []


def test_wants_tools_is_false_for_a_plain_answer():
    assert LLMResponse(content="All done.").wants_tools is False


def test_wants_tools_is_true_when_any_tool_call_is_present():
    call = ToolCall(name="get_products", arguments={"sku": "RUN-001"})
    response = LLMResponse(tool_calls=[call])
    assert response.wants_tools is True


# --- ToolCall ---------------------------------------------------------------


def test_tool_call_generates_a_distinct_12_char_hex_id_by_default():
    first = ToolCall(name="a", arguments={})
    second = ToolCall(name="a", arguments={})
    assert len(first.id) == 12
    assert all(c in "0123456789abcdef" for c in first.id) is True
    assert first.id != second.id


def test_tool_call_to_dict_and_from_dict_round_trip():
    call = ToolCall(name="get_products", arguments={"sku": "RUN-001"}, id="abc123def456")
    data = {
        "id": "abc123def456",
        "name": "get_products",
        "arguments": {"sku": "RUN-001"},
    }
    assert call.to_dict() == data
    assert ToolCall.from_dict(data) == call


def test_tool_call_from_dict_fills_missing_arguments_and_id():
    call = ToolCall.from_dict({"name": "ping"})
    assert call.name == "ping"
    assert call.arguments == {}
    assert len(call.id) == 12

    explicit_none = ToolCall.from_dict({"name": "ping", "arguments": None})
    assert explicit_none.arguments == {}


# --- Message -----------------------------------------------------------------


def test_message_to_dict_omits_unset_optional_keys():
    message = Message(role="user", content="hello")
    assert message.to_dict() == {"role": "user", "content": "hello"}


def test_message_to_dict_includes_tool_fields_when_set():
    call = ToolCall(name="get_products", arguments={"sku": "RUN-001"}, id="abc123def456")
    message = Message(
        role="assistant",
        content="",
        tool_calls=[call],
        tool_call_id="tool-1",
        name="assistant-turn",
    )
    assert message.to_dict() == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "abc123def456", "name": "get_products", "arguments": {"sku": "RUN-001"}}
        ],
        "tool_call_id": "tool-1",
        "name": "assistant-turn",
    }


def test_message_from_dict_defaults_missing_fields():
    message = Message.from_dict({"role": "system"})
    assert message.role == "system"
    assert message.content == ""
    assert message.tool_calls == []
    assert message.tool_call_id is None
    assert message.name is None


def test_message_round_trip_preserves_nested_tool_calls():
    original = Message(
        role="assistant",
        content="checking",
        tool_calls=[ToolCall(name="get_products", arguments={"sku": "RUN-001"}, id="abc123def456")],
    )
    assert Message.from_dict(original.to_dict()) == original


def test_tool_result_with_string_payload_keeps_the_text_verbatim():
    call = ToolCall(name="get_products", arguments={}, id="call-1")
    result = Message.tool_result(call, "plain text answer")
    assert result.role == "tool"
    assert result.content == "plain text answer"
    assert result.tool_call_id == "call-1"
    assert result.name == "get_products"


def test_tool_result_json_encodes_non_string_payloads():
    call = ToolCall(name="get_products", arguments={}, id="call-2")
    result = Message.tool_result(call, {"sku": "RUN-001"})
    assert result.content == '{"sku": "RUN-001"}'


def test_tool_result_uses_default_str_for_unserializable_payloads():
    call = ToolCall(name="get_products", arguments={}, id="call-3")
    result = Message.tool_result(call, datetime(2026, 10, 24))
    assert result.content == '"2026-10-24 00:00:00"'


# --- parse_arguments ----------------------------------------------------------


def test_parse_arguments_returns_dicts_unchanged():
    payload = {"sku": "RUN-001"}
    assert parse_arguments(payload) is payload


def test_parse_arguments_accepts_a_json_object_string_with_whitespace():
    raw = '  {"sku": "RUN-001", "page": 2}  '
    assert parse_arguments(raw) == {"sku": "RUN-001", "page": 2}


def test_parse_arguments_rejects_invalid_json_with_the_raw_snippet():
    raw = "{not json"
    with pytest.raises(LLMResponseError) as excinfo:
        parse_arguments(raw)
    assert str(excinfo.value) == f"Tool arguments were not valid JSON: {raw}"


@pytest.mark.parametrize("raw", ['[1, 2]', '"just a string"', "42"])
def test_parse_arguments_rejects_json_that_is_not_an_object(raw):
    with pytest.raises(LLMResponseError, match="Tool arguments must be a JSON object"):
        parse_arguments(raw)


@pytest.mark.parametrize("raw", ["", "   \n\t  "])
def test_parse_arguments_returns_empty_dict_for_blank_strings(raw):
    assert parse_arguments(raw) == {}


@pytest.mark.parametrize("raw", [42, None, ["a"]])
def test_parse_arguments_returns_empty_dict_for_non_str_non_dict_input(raw):
    assert parse_arguments(raw) == {}


# --- ProviderStatus / LLMProvider ---------------------------------------------


def test_provider_status_stores_fields_and_defaults_detail_to_none():
    status = ProviderStatus(True, "ollama", "llama3")
    assert status.available is True
    assert status.provider == "ollama"
    assert status.model == "llama3"
    assert status.detail is None

    down = ProviderStatus(False, "openai", "gpt-4o-mini", detail="HTTP 503")
    assert down.detail == "HTTP 503"


def test_llm_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError, match="abstract"):
        LLMProvider()

