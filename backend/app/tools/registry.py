"""Tool registry.

Every capability the agent has is a `Tool`: a name, a description, a
validated input schema, an output schema, a read/write classification and an
implementation. The classification is the safety boundary - the engine
executes READ tools immediately and routes WRITE tools through human
approval. Nothing else in the system decides that.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.integrations.base import EcommerceProvider
from app.llm.base import LLMProvider
from app.logging_config import get_logger
from app.schemas.domain import ToolInfo

logger = get_logger(__name__)

TInput = TypeVar("TInput", bound=BaseModel)


class ToolAccess(StrEnum):
    READ = "read"
    WRITE = "write"


class ToolError(RuntimeError):
    """A tool failed in a way the agent should see and can react to."""


class ToolNotFoundError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


@dataclass(slots=True)
class ToolContext:
    """Everything a tool implementation is allowed to touch."""

    db: Session
    provider: EcommerceProvider
    llm: LLMProvider


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    category: str
    access: ToolAccess
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[[ToolContext, Any], BaseModel]

    @property
    def requires_approval(self) -> bool:
        return self.access is ToolAccess.WRITE

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()

    def to_llm_spec(self) -> dict[str, Any]:
        """OpenAI/Ollama function-tool format."""
        schema = self.input_schema
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
            },
        }

    def to_info(self) -> ToolInfo:
        return ToolInfo(
            name=self.name,
            description=self.description,
            category=self.category,
            access=self.access.value,
            requires_approval=self.requires_approval,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # --- registration ---------------------------------------------------
    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def tool(
        self,
        *,
        name: str,
        description: str,
        category: str,
        access: ToolAccess,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
    ) -> Callable[[Callable[[ToolContext, Any], BaseModel]], Callable[..., BaseModel]]:
        def decorator(fn: Callable[[ToolContext, Any], BaseModel]):
            self.register(
                Tool(
                    name=name,
                    description=description,
                    category=category,
                    access=access,
                    input_model=input_model,
                    output_model=output_model,
                    fn=fn,
                )
            )
            return fn

        return decorator

    # --- lookup ---------------------------------------------------------
    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(
                f"Unknown tool '{name}'. Available tools: {', '.join(sorted(self._tools))}"
            ) from None

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self, *, access: ToolAccess | None = None) -> list[Tool]:
        tools = sorted(self._tools.values(), key=lambda t: (t.category, t.name))
        if access is not None:
            tools = [t for t in tools if t.access is access]
        return tools

    def llm_specs(self) -> list[dict[str, Any]]:
        return [t.to_llm_spec() for t in self.list()]

    def __len__(self) -> int:
        return len(self._tools)

    # --- execution ------------------------------------------------------
    def validate(self, name: str, arguments: dict[str, Any]) -> BaseModel:
        tool = self.get(name)
        try:
            return tool.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolValidationError(f"Invalid arguments for '{name}' - {problems}") from exc

    def execute(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
        """Validate, run, and return a JSON-serialisable result.

        Tool failures surface as `ToolError` so the engine can feed them back
        to the model instead of aborting the run.
        """
        tool = self.get(name)
        params = self.validate(name, arguments)
        logger.info("Tool executed: %s", name)
        try:
            result = tool.fn(ctx, params)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate boundary
            logger.exception("Tool '%s' raised", name)
            raise ToolError(f"{name} failed: {exc}") from exc

        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        return result
