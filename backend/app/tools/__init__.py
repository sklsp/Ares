"""Tool package: builds the single registry the agent and the MCP server share."""

from __future__ import annotations

from functools import lru_cache

from app.tools import agent_tools, analytics, content, intelligence, inventory, products
from app.tools.registry import (
    Tool,
    ToolAccess,
    ToolContext,
    ToolError,
    ToolNotFoundError,
    ToolRegistry,
    ToolValidationError,
)

MODULES = (products, inventory, analytics, content, intelligence, agent_tools)


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for module in MODULES:
        module.register(registry)
    return registry


@lru_cache
def get_registry() -> ToolRegistry:
    """Process-wide registry. Tools are stateless - state lives in ToolContext."""
    return build_registry()


__all__ = [
    "Tool",
    "ToolAccess",
    "ToolContext",
    "ToolError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolValidationError",
    "build_registry",
    "get_registry",
]
