"""LLM provider selection and honest health reporting."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import Message
from app.llm.deterministic import DeterministicProvider
from app.llm.factory import build_llm_provider


def test_test_provider_is_selected_and_labeled():
    provider = build_llm_provider(Settings(llm_provider="test"))
    assert isinstance(provider, DeterministicProvider)
    status = provider.status()
    assert status.available is True
    # Must be unmistakable that this is not a live model.
    assert "deterministic" in status.provider
    assert "not a live model" in status.provider


def test_deterministic_provider_reports_tool_data_without_inventing_facts():
    import json

    provider = DeterministicProvider()
    tool_payload = json.dumps({"count": 4, "sku": "RUN-001"})
    response = provider.chat([
        Message(role="user", content="How many products?"),
        Message(role="tool", content=tool_payload, tool_call_id="t1", name="get_products"),
    ])
    assert "RUN-001" in response.content
    assert response.tool_calls == []


def test_ollama_provider_still_constructible():
    from app.llm.ollama import OllamaProvider

    provider = build_llm_provider(Settings(
        llm_provider="ollama",
        ollama_base_url="http://localhost:11434",
        ollama_model="llama3.2",
    ))
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "llama3.2"


def test_unknown_provider_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_llm_provider(Settings(llm_provider="does_not_exist"))
