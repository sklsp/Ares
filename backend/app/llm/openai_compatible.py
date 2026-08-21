"""OpenAI-compatible chat provider.

Works with the OpenAI API itself and with anything that speaks the same
`/chat/completions` shape (vLLM, LM Studio, OpenRouter, Together, ...).
Set LLM_PROVIDER=openai_compatible plus OPENAI_BASE_URL / OPENAI_API_KEY.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.base import (
    LLMProvider,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    ProviderStatus,
    ToolCall,
    parse_arguments,
)


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self, base_url: str, model: str, api_key: str | None = None, timeout: float = 120.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_openai_message(m) for m in messages],
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=self._headers
                )
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Cannot reach {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise LLMResponseError(
                f"LLM backend returned {response.status_code}: {response.text[:300]}"
            )

        choices = response.json().get("choices") or []
        if not choices:
            raise LLMResponseError("LLM backend returned no choices")

        message = choices[0].get("message") or {}
        calls = [
            ToolCall(
                id=raw.get("id") or "",
                name=(raw.get("function") or {}).get("name", ""),
                arguments=parse_arguments((raw.get("function") or {}).get("arguments")),
            )
            for raw in message.get("tool_calls") or []
        ]
        return LLMResponse(
            content=(message.get("content") or "").strip(),
            tool_calls=[c for c in calls if c.name],
        )

    def status(self) -> ProviderStatus:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ProviderStatus(False, self.name, self.model, f"unreachable: {exc}")
        return ProviderStatus(True, self.name, self.model)


def _to_openai_message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id or "",
        }
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for tc in message.tool_calls
        ]
    return data
