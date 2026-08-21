"""A scripted LLM provider.

Lets the tests drive the agent through exact tool sequences - including
failure modes - without a model server.
"""

from __future__ import annotations

from typing import Any

from app.llm.base import (
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    Message,
    ProviderStatus,
    ToolCall,
)


def tool_call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name=name, arguments=arguments)])


def tool_calls(*calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(name=n, arguments=a) for n, a in calls])


def answer(text: str) -> LLMResponse:
    return LLMResponse(content=text)


class FakeLLM(LLMProvider):
    """Returns queued responses in order; falls back to a canned final answer."""

    name = "fake"
    model = "scripted"

    def __init__(self, *responses: LLMResponse, available: bool = True) -> None:
        self.queue: list[LLMResponse] = list(responses)
        self.calls: list[list[Message]] = []
        self.tool_specs: list[list[dict[str, Any]]] = []
        self.available = available
        self.fallback = answer("Done.")

    def script(self, *responses: LLMResponse) -> "FakeLLM":
        self.queue.extend(responses)
        return self

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        if not self.available:
            raise LLMUnavailableError("Cannot reach the fake model")
        self.calls.append(list(messages))
        self.tool_specs.append(list(tools or []))
        if self.queue:
            return self.queue.pop(0)
        return self.fallback

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.available, self.name, self.model)

    # --- assertions helpers -------------------------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_tool_results(self) -> list[Message]:
        if not self.calls:
            return []
        return [m for m in self.calls[-1] if m.role == "tool"]

    def offered_tool_names(self) -> set[str]:
        if not self.tool_specs:
            return set()
        return {spec["function"]["name"] for spec in self.tool_specs[-1]}
