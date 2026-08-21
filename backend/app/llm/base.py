"""Provider-agnostic LLM types.

The agent engine imports only from this module - never from `ollama.py` -
so a different backend is a config change, not a rewrite.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


class LLMError(RuntimeError):
    """Base class for all LLM failures."""


class LLMUnavailableError(LLMError):
    """The model backend could not be reached."""


class LLMResponseError(LLMError):
    """The backend replied, but not with something usable."""


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        return cls(
            name=data["name"],
            arguments=data.get("arguments") or {},
            id=data.get("id") or uuid.uuid4().hex[:12],
        )


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            role=data["role"],
            content=data.get("content") or "",
            tool_calls=[ToolCall.from_dict(tc) for tc in data.get("tool_calls") or []],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )

    @classmethod
    def tool_result(cls, call: ToolCall, payload: Any) -> Message:
        content = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        return cls(role="tool", content=content, tool_call_id=call.id, name=call.name)


@dataclass(slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(slots=True)
class ProviderStatus:
    available: bool
    provider: str
    model: str
    detail: str | None = None


class LLMProvider(ABC):
    """Chat completion with tool calling."""

    name: str = "abstract"
    model: str = ""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """One completion. Raises LLMUnavailableError / LLMResponseError."""

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Cheap reachability probe used by /health."""


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Models return tool arguments as a dict or as a JSON string. Accept both."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(f"Tool arguments were not valid JSON: {raw[:200]}") from exc
        if not isinstance(parsed, dict):
            raise LLMResponseError("Tool arguments must be a JSON object")
        return parsed
    return {}
