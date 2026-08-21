"""Deterministic LLM provider for CI and offline development.

Implements the same interface as Ollama/OpenAI providers so the complete
orchestration layer (agent loop, tool calling, validation) can be verified
without live inference. It is explicitly labeled `test/deterministic` in
every status report so it can never be mistaken for a real model, and it is
never selected unless LLM_PROVIDER=test is set deliberately.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.base import LLMProvider, LLMResponse, Message, ProviderStatus

# A deterministic catalog-aware responder: answers from tool results that are
# already in the conversation rather than inventing facts.
DEFAULT_RESPONSE = (
    "Based on the tool results in this conversation: the store data retrieved "
    "above reflects what was queried. No further tool calls are needed."
)


class DeterministicProvider(LLMProvider):
    name = "deterministic"
    model = "ci-fixture"

    def __init__(self, scripted_responses: list[str] | None = None) -> None:
        self.scripted = list(scripted_responses or [])
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        self.calls += 1
        if self.scripted:
            return LLMResponse(content=self.scripted.pop(0))
        # Summarize whatever tool data exists in the conversation without
        # fabricating specifics beyond it.
        tool_messages = [m for m in messages if m.role == "tool"]
        if tool_messages:
            try:
                payload = json.loads(tool_messages[-1].content)
                summary = json.dumps(payload)[:400]
            except (ValueError, TypeError):
                summary = (tool_messages[-1].content or "")[:400]
            return LLMResponse(content=f"Tool result received: {summary}")
        return LLMResponse(content=DEFAULT_RESPONSE)

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            available=True,
            provider=f"{self.name} (test/deterministic — not a live model)",
            model=self.model,
            detail="Deterministic CI provider; set LLM_PROVIDER=ollama for real inference.",
        )
