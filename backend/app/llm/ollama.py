"""Ollama chat provider (https://github.com/ollama/ollama/blob/main/docs/api.md)."""

from __future__ import annotations

import json
import re
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
from app.logging_config import get_logger

logger = get_logger(__name__)

JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama_message(m) for m in messages],
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is it running? ({exc})"
            ) from exc

        if response.status_code == 404:
            raise LLMUnavailableError(
                f"Ollama has no model named '{self.model}'. Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            raise LLMResponseError(
                f"Ollama returned {response.status_code}: {response.text[:300]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMResponseError("Ollama returned a non-JSON body") from exc

        message = body.get("message") or {}
        content = (message.get("content") or "").strip()
        calls = _extract_tool_calls(message)

        if not calls and tools and content:
            calls = _recover_tool_call_from_text(content, {t["function"]["name"] for t in tools})
            if calls:
                logger.debug("Recovered tool call from plain-text response")
                content = ""

        return LLMResponse(content=content, tool_calls=calls)

    # ------------------------------------------------------------------
    def status(self) -> ProviderStatus:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                names = {m.get("name", "") for m in response.json().get("models", [])}
        except httpx.HTTPError as exc:
            return ProviderStatus(False, self.name, self.model, f"unreachable: {exc}")

        installed = any(n == self.model or n.split(":")[0] == self.model for n in names)
        detail = None if installed else f"model '{self.model}' not pulled"
        return ProviderStatus(installed, self.name, self.model, detail)


# ----------------------------------------------------------------------
def _to_ollama_message(message: Message) -> dict[str, Any]:
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in message.tool_calls
        ]
    if message.role == "tool" and message.name:
        # Ollama matches tool results by name rather than by call id.
        data["name"] = message.name
    return data


def _extract_tool_calls(message: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        calls.append(ToolCall(name=name, arguments=parse_arguments(function.get("arguments"))))
    return calls


def _recover_tool_call_from_text(content: str, known_tools: set[str]) -> list[ToolCall]:
    """Smaller models sometimes emit the tool call as text instead of using the
    tool-call field. Recover it rather than failing the whole run."""
    candidates = JSON_BLOCK.findall(content)
    if not candidates:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            candidates = [content[start : end + 1]]

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        name = data.get("name") or data.get("tool") or data.get("function")
        if isinstance(name, str) and name in known_tools:
            args = data.get("arguments") or data.get("parameters") or {}
            if isinstance(args, (dict, str)):
                return [ToolCall(name=name, arguments=parse_arguments(args))]
    return []
