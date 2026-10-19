"""Deterministic LLM providers for offline evaluation runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.evals.tasks import EvalTask
from app.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ProviderStatus,
    ToolCall,
)

DRAFT = (
    "Drafted for evaluation runs: a clear product description that names the material,"
    " the size, who it is for and when to use it."
)


def _resolve(value: Any, resolve_id: Callable[[str], int]) -> Any:
    """Replace an "@id:SKU" placeholder with the id *resolve_id* looks up."""
    if isinstance(value, str) and value.startswith("@id:"):
        return resolve_id(value.removeprefix("@id:"))
    return value


class OracleProvider(LLMProvider):
    """Replays a task's oracle script: one tool call per chat, then the answer.

    A chat without tools (a copy-writing step such as
    generate_product_description) returns DRAFT and does not advance the
    script, so the remaining calls keep their order.
    """

    name = "oracle"
    model = "task-script"

    def __init__(self, task: EvalTask, resolve_id: Callable[[str], int]) -> None:
        self._task = task
        self._resolve_id = resolve_id
        self._next_call = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        if not tools:
            return LLMResponse(content=DRAFT)
        if self._next_call >= len(self._task.oracle):
            return LLMResponse(content=self._task.oracle_answer)
        call = self._task.oracle[self._next_call]
        self._next_call += 1
        args = {key: _resolve(value, self._resolve_id) for key, value in call.args.items()}
        return LLMResponse(tool_calls=[ToolCall(name=call.tool, arguments=args)])

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "oracle", "task-script")


class LazyProvider(LLMProvider):
    """Never calls tools; always gives the same non-committal answer."""

    name = "lazy"
    model = "no-tools"

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float = 0.1,
    ) -> LLMResponse:
        return LLMResponse(content="I am not sure.")

    def status(self) -> ProviderStatus:
        return ProviderStatus(True, "lazy", "no-tools")
