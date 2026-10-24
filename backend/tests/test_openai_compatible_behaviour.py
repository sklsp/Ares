"""Behavioural tests for ``app.llm.openai_compatible`` (OpenAICompatibleProvider).

The provider builds its own ``httpx.Client`` inside each call, so the module's
``httpx`` symbol is monkeypatched with a fake that records every request (URL,
payload and headers) and replays canned responses in order. No network access;
every assertion uses literal expected values.
"""

from __future__ import annotations

import pytest

from app.llm.base import LLMResponseError, LLMUnavailableError, Message, ToolCall
from app.llm.openai_compatible import OpenAICompatibleProvider


class FakeHTTPError(Exception):
    """Stands in for ``httpx.HTTPError`` (the module catches it by name)."""


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise FakeHTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeClient:
    """Records requests and replays the fake module's queued responses."""

    def __init__(self, httpx_fake, timeout=None):
        self.timeout = timeout
        self.requests = []
        self._httpx = httpx_fake

    def post(self, url, json=None, headers=None):
        self.requests.append(("POST", url, json, headers))
        return self._httpx.next()

    def get(self, url, headers=None):
        self.requests.append(("GET", url, None, headers))
        return self._httpx.next()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeHttpx:
    """Module-level stand-in for the ``httpx`` package used by app.llm.openai_compatible."""

    HTTPError = FakeHTTPError

    def __init__(self, *responses):
        self.queue = list(responses)
        self.clients = []

    def Client(self, timeout=None):
        client = FakeClient(self, timeout=timeout)
        self.clients.append(client)
        return client

    def next(self):
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def install(monkeypatch, *responses):
    fake = FakeHttpx(*responses)
    monkeypatch.setattr("app.llm.openai_compatible.httpx", fake)
    return fake


def make_provider(base_url="http://localhost:8080/", model="test-model", api_key=None):
    return OpenAICompatibleProvider(base_url=base_url, model=model, api_key=api_key)


TOOLS = [{"type": "function", "function": {"name": "get_products"}}]


# --------------------------------------------------------------------------- #
# construction and headers
# --------------------------------------------------------------------------- #


def test_init_strips_trailing_slash_and_defaults():
    provider = make_provider()

    assert provider.base_url == "http://localhost:8080"
    assert provider.model == "test-model"
    assert provider.api_key is None
    assert provider.timeout == 120.0


def test_headers_with_and_without_api_key():
    with_key = make_provider(api_key="sk-test-123")
    without_key = make_provider()

    assert with_key._headers == {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-test-123",
    }
    assert without_key._headers == {"Content-Type": "application/json"}


# --------------------------------------------------------------------------- #
# chat request payload and message serialization
# --------------------------------------------------------------------------- #


def test_chat_posts_literal_payload_without_tools(monkeypatch):
    body = {"choices": [{"message": {"role": "assistant", "content": "  hello  "}}]}
    fake = install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="Hello")])

    assert len(fake.clients) == 1
    method, url, payload, headers = fake.clients[0].requests[0]
    assert (method, url) == ("POST", "http://localhost:8080/chat/completions")
    assert headers == {"Content-Type": "application/json"}
    assert payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "temperature": 0.1,
    }
    assert "tools" not in payload and "tool_choice" not in payload
    assert response.content == "hello"
    assert response.tool_calls == []


def test_chat_includes_tools_and_tool_choice(monkeypatch):
    body = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    fake = install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="Find it")], TOOLS, temperature=0.7)

    payload = fake.clients[0].requests[0][2]
    assert payload["tools"] == TOOLS
    assert payload["tool_choice"] == "auto"
    assert payload["temperature"] == 0.7
    assert response.content == ""


def test_chat_serializes_assistant_tool_calls_and_tool_messages(monkeypatch):
    body = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    fake = install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()
    call = ToolCall(name="get_products", arguments={"sku": "RUN-001"}, id="call-abc")

    provider.chat(
        [
            Message(role="system", content="You are an assistant."),
            Message(role="user", content="Find it"),
            Message(role="assistant", content="", tool_calls=[call]),
            Message(role="tool", content='{"ok": true}', tool_call_id="call-abc"),
            Message(role="tool", content="no id here"),
        ]
    )

    payload = fake.clients[0].requests[0][2]
    assert payload["messages"] == [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Find it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-abc",
                    "type": "function",
                    "function": {"name": "get_products", "arguments": '{"sku": "RUN-001"}'},
                }
            ],
        },
        {"role": "tool", "content": '{"ok": true}', "tool_call_id": "call-abc"},
        {"role": "tool", "content": "no id here", "tool_call_id": ""},
    ]




# --------------------------------------------------------------------------- #
# chat error paths
# --------------------------------------------------------------------------- #


def test_chat_connection_error_raises_unavailable(monkeypatch):
    install(monkeypatch, FakeHTTPError("connect refused"))
    provider = make_provider()

    with pytest.raises(LLMUnavailableError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "Cannot reach http://localhost:8080: connect refused"


def test_chat_http_401_raises_response_error_with_truncated_body(monkeypatch):
    install(monkeypatch, FakeResponse(status_code=401, text="y" * 500))
    provider = make_provider()

    with pytest.raises(LLMResponseError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "LLM backend returned 401: " + "y" * 300


def test_chat_missing_choices_key_raises_no_choices(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={}))
    provider = make_provider()

    with pytest.raises(LLMResponseError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "LLM backend returned no choices"


def test_chat_empty_choices_list_raises_no_choices(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={"choices": []}))
    provider = make_provider()

    with pytest.raises(LLMResponseError, match="no choices"):
        provider.chat([Message(role="user", content="hi")])


# --------------------------------------------------------------------------- #
# response parsing
# --------------------------------------------------------------------------- #


def test_chat_extracts_tool_calls_with_json_string_arguments(monkeypatch):
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-1", "function": {"name": "get_products", "arguments": '{"sku": "RUN-001"}'}},
                        {"id": "call-2", "function": {"name": "search_orders", "arguments": {"limit": 5}}},
                    ],
                }
            }
        ]
    }
    install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")])

    assert [c.name for c in response.tool_calls] == ["get_products", "search_orders"]
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].arguments == {"sku": "RUN-001"}
    assert response.tool_calls[1].arguments == {"limit": 5}


def test_chat_filters_nameless_tool_calls(monkeypatch):
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call-1", "function": {}},
                        {"id": "call-2", "no_function_key": True},
                        {"id": "call-3", "function": {"name": "get_products", "arguments": "{}"}},
                    ],
                }
            }
        ]
    }
    install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")])

    assert len(response.tool_calls) == 1
    assert (response.tool_calls[0].id, response.tool_calls[0].name) == ("call-3", "get_products")


def test_chat_missing_message_key_yields_empty_response(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={"choices": [{}]}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")])

    assert response.content == ""
    assert response.tool_calls == []


def test_chat_invalid_json_arguments_raise_response_error(monkeypatch):
    body = {
        "choices": [
            {"message": {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "get_products", "arguments": "{bad"}}]}}
        ]
    }
    install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    with pytest.raises(LLMResponseError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "Tool arguments were not valid JSON: {bad"


def test_chat_non_object_json_arguments_raise_response_error(monkeypatch):
    body = {
        "choices": [
            {"message": {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "get_products", "arguments": "[1, 2]"}}]}}
        ]
    }
    install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    with pytest.raises(LLMResponseError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "Tool arguments must be a JSON object"


# --------------------------------------------------------------------------- #
# status() health reporting
# --------------------------------------------------------------------------- #


def test_status_available_on_200(monkeypatch):
    fake = install(monkeypatch, FakeResponse(json_data={"data": []}))
    provider = make_provider(api_key="sk-test-123")

    status = provider.status()

    method, url, _, headers = fake.clients[0].requests[0]
    assert (method, url) == ("GET", "http://localhost:8080/models")
    assert headers["Authorization"] == "Bearer sk-test-123"
    assert fake.clients[0].timeout == 5.0
    assert (status.available, status.provider, status.model) == (True, "openai_compatible", "test-model")
    assert status.detail is None


def test_status_unreachable_on_connection_error(monkeypatch):
    install(monkeypatch, FakeHTTPError("refused"))
    provider = make_provider()

    status = provider.status()

    assert status.available is False
    assert status.detail == "unreachable: refused"


def test_status_unreachable_on_http_500(monkeypatch):
    install(monkeypatch, FakeResponse(status_code=500))
    provider = make_provider()

    status = provider.status()

    assert status.available is False
    assert status.detail == "unreachable: HTTP 500"


