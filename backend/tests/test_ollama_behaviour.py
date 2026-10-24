"""Behavioural tests for ``app.llm.ollama`` (OllamaProvider).

The provider builds its own ``httpx.Client`` inside each call, so the module's
``httpx`` symbol is monkeypatched with a fake that records every request and
replays canned responses (or raises) in order. No network access is involved;
every assertion uses literal expected values for URLs, payloads, messages and
status fields.
"""

from __future__ import annotations

import pytest

from app.llm.base import LLMResponseError, LLMUnavailableError, Message, ToolCall
from app.llm.ollama import OllamaProvider


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


class NonJSONResponse(FakeResponse):
    """A body whose ``.json()`` raises, like a non-JSON HTTP response."""

    def json(self):
        raise ValueError("no JSON in body")


class FakeClient:
    """Records requests and replays the fake module's queued responses."""

    def __init__(self, httpx_fake, timeout=None):
        self.timeout = timeout
        self.requests = []
        self._httpx = httpx_fake

    def post(self, url, json=None):
        self.requests.append(("POST", url, json))
        return self._httpx.next()

    def get(self, url):
        self.requests.append(("GET", url, None))
        return self._httpx.next()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeHttpx:
    """Module-level stand-in for the ``httpx`` package used by app.llm.ollama."""

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
    monkeypatch.setattr("app.llm.ollama.httpx", fake)
    return fake


def make_provider(base_url="http://localhost:11434/", model="test-model"):
    return OllamaProvider(base_url=base_url, model=model)


TOOLS = [{"type": "function", "function": {"name": "get_products"}}]


# --------------------------------------------------------------------------- #
# construction and request payload
# --------------------------------------------------------------------------- #


def test_init_strips_trailing_slash_and_defaults_timeout():
    provider = make_provider()

    assert provider.base_url == "http://localhost:11434"
    assert provider.model == "test-model"
    assert provider.timeout == 120.0


def test_chat_posts_literal_payload_without_tools(monkeypatch):
    fake = install(
        monkeypatch,
        FakeResponse(json_data={"message": {"role": "assistant", "content": "  hello  "}}),
    )
    provider = make_provider()

    response = provider.chat([Message(role="user", content="Hello")])

    assert len(fake.clients) == 1
    method, url, payload = fake.clients[0].requests[0]
    assert (method, url) == ("POST", "http://localhost:11434/api/chat")
    assert payload == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    assert "tools" not in payload
    assert response.content == "hello"
    assert response.tool_calls == []


def test_chat_includes_tools_and_full_message_shapes(monkeypatch):
    fake = install(
        monkeypatch,
        FakeResponse(json_data={"message": {"role": "assistant", "content": ""}}),
    )
    provider = make_provider()
    call = ToolCall(name="get_products", arguments={"sku": "RUN-001"})

    response = provider.chat(
        [
            Message(role="user", content="Find it"),
            Message(role="assistant", content="", tool_calls=[call]),
            Message(role="tool", content='{"ok": true}', name="get_products"),
        ],
        TOOLS,
        temperature=0.7,
    )

    payload = fake.clients[0].requests[0][2]
    assert payload["tools"] == TOOLS
    assert payload["options"] == {"temperature": 0.7}
    assert payload["messages"] == [
        {"role": "user", "content": "Find it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_products", "arguments": {"sku": "RUN-001"}}}],
        },
        {"role": "tool", "content": '{"ok": true}', "name": "get_products"},
    ]
    assert response.content == ""
    assert response.tool_calls == []


# --------------------------------------------------------------------------- #
# chat error paths
# --------------------------------------------------------------------------- #


def test_chat_connection_error_raises_unavailable(monkeypatch):
    install(monkeypatch, FakeHTTPError("connect refused"))
    provider = make_provider()

    with pytest.raises(LLMUnavailableError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == (
        "Cannot reach Ollama at http://localhost:11434. Is it running? (connect refused)"
    )


def test_chat_404_raises_model_not_pulled(monkeypatch):
    install(monkeypatch, FakeResponse(status_code=404))
    provider = make_provider()

    with pytest.raises(LLMUnavailableError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "Ollama has no model named 'test-model'. Run: ollama pull test-model"


def test_chat_server_error_truncates_body_to_300_chars(monkeypatch):
    install(monkeypatch, FakeResponse(status_code=500, text="x" * 500))
    provider = make_provider()

    with pytest.raises(LLMResponseError) as excinfo:
        provider.chat([Message(role="user", content="hi")])

    assert str(excinfo.value) == "Ollama returned 500: " + "x" * 300


def test_chat_non_json_body_raises_response_error(monkeypatch):
    install(monkeypatch, NonJSONResponse(status_code=200))
    provider = make_provider()

    with pytest.raises(LLMResponseError, match="Ollama returned a non-JSON body"):
        provider.chat([Message(role="user", content="hi")])


# --------------------------------------------------------------------------- #
# response parsing
# --------------------------------------------------------------------------- #


def test_chat_handles_body_without_message_key(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={"done": True}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")])

    assert response.content == ""
    assert response.tool_calls == []


def test_chat_extracts_structured_tool_calls_and_skips_nameless(monkeypatch):
    body = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_products", "arguments": '{"sku": "RUN-001"}'}},
                {"function": {}},
                {"no_function_key": True},
            ],
        }
    }
    install(monkeypatch, FakeResponse(json_data=body))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")])

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "get_products"
    assert call.arguments == {"sku": "RUN-001"}


# --------------------------------------------------------------------------- #
# tool-call recovery from plain text (small models)
# --------------------------------------------------------------------------- #

FENCED_CALL = 'Sure!\n```json\n{"name": "get_products", "arguments": {"query": "shoes"}}\n```\nDone.'


def test_chat_recovers_fenced_json_tool_call(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={"message": {"role": "assistant", "content": FENCED_CALL}}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="Find shoes")], TOOLS)

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_products"
    assert response.tool_calls[0].arguments == {"query": "shoes"}
    assert response.content == ""


def test_chat_recovers_bare_json_with_tool_key_and_parameters(monkeypatch):
    content = 'I will call it: {"tool": "search_orders", "parameters": {"limit": 5}}'
    tools = [{"type": "function", "function": {"name": "search_orders"}}]
    install(monkeypatch, FakeResponse(json_data={"message": {"role": "assistant", "content": content}}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="orders")], tools)

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search_orders"
    assert response.tool_calls[0].arguments == {"limit": 5}
    assert response.content == ""


def test_chat_recovery_accepts_function_key(monkeypatch):
    content = '```json\n{"function": "get_products", "parameters": {"q": 1}}\n```'
    install(monkeypatch, FakeResponse(json_data={"message": {"role": "assistant", "content": content}}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")], TOOLS)

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "get_products"
    assert response.tool_calls[0].arguments == {"q": 1}


def test_chat_recovery_ignores_unknown_tool_names(monkeypatch):
    content = '```json\n{"name": "unknown_tool", "arguments": {}}\n```'
    install(monkeypatch, FakeResponse(json_data={"message": {"role": "assistant", "content": content}}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")], TOOLS)

    assert response.tool_calls == []
    assert response.content == content


def test_chat_recovery_skips_invalid_json_and_non_dict_arguments(monkeypatch):
    content = '```{not json}\n```\n```json\n{"name": "get_products", "arguments": 5}\n```'
    install(monkeypatch, FakeResponse(json_data={"message": {"role": "assistant", "content": content}}))
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")], TOOLS)

    assert response.tool_calls == []
    assert response.content == content


def test_chat_recovery_no_json_in_text_keeps_content(monkeypatch):
    install(
        monkeypatch,
        FakeResponse(json_data={"message": {"role": "assistant", "content": "I cannot help with that."}}),
    )
    provider = make_provider()

    response = provider.chat([Message(role="user", content="hi")], TOOLS)

    assert response.tool_calls == []
    assert response.content == "I cannot help with that."


# --------------------------------------------------------------------------- #
# status() health reporting
# --------------------------------------------------------------------------- #


def test_status_unreachable_on_connection_error(monkeypatch):
    install(monkeypatch, FakeHTTPError("refused"))
    provider = make_provider()

    status = provider.status()

    assert (status.available, status.provider, status.model) == (False, "ollama", "test-model")
    assert status.detail == "unreachable: refused"


def test_status_unreachable_on_http_500(monkeypatch):
    install(monkeypatch, FakeResponse(status_code=500))
    provider = make_provider()

    status = provider.status()

    assert status.available is False
    assert status.detail == "unreachable: HTTP 500"


def test_status_model_installed_exact_match(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={"models": [{"name": "test-model"}]}))
    provider = make_provider()

    status = provider.status()

    assert status.available is True
    assert status.detail is None


def test_status_model_installed_by_tag_prefix(monkeypatch):
    install(
        monkeypatch,
        FakeResponse(json_data={"models": [{"name": "llama3.2:latest"}, {"name": "other"}]}),
    )
    provider = make_provider(model="llama3.2")

    status = provider.status()

    assert status.available is True
    assert status.detail is None


def test_status_model_not_pulled(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={"models": [{"name": "other"}]}))
    provider = make_provider()

    status = provider.status()

    assert status.available is False
    assert status.detail == "model 'test-model' not pulled"


def test_status_empty_model_list_reports_not_pulled(monkeypatch):
    install(monkeypatch, FakeResponse(json_data={}))
    provider = make_provider()

    status = provider.status()

    assert status.available is False
    assert status.detail == "model 'test-model' not pulled"



