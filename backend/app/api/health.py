"""Health and capability endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.deps import LLM, DbSession
from app.config import settings
from app.schemas.api import HealthResponse, MCPHealthResponse
from app.schemas.domain import ToolInfo
from app.tools import get_registry

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(db: DbSession, llm: LLM) -> HealthResponse:
    """Liveness plus dependency status. Always 200 - read the body for detail."""
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001
        database = f"error: {exc}"

    status = llm.status()
    return HealthResponse(
        status="ok" if database == "ok" and status.available else "degraded",
        version=__version__,
        database=database,
        llm={
            "provider": status.provider,
            "model": status.model,
            "available": status.available,
            "detail": status.detail,
        },
    )


@router.get("/tools", response_model=list[ToolInfo])
def list_tools() -> list[ToolInfo]:
    """Every tool the agent can use, with its schemas and read/write class."""
    return [tool.to_info() for tool in get_registry().list()]


@router.get("/mcp/health", response_model=MCPHealthResponse)
def mcp_health() -> MCPHealthResponse:
    """Reports the real MCP server: it is imported here and asked for its tools."""
    try:
        from mcp_server import TOOL_NAMES, build_server

        server = build_server()
        return MCPHealthResponse(
            status="ok",
            server_name=server.name,
            transport="stdio",
            tool_count=len(TOOL_NAMES),
            tools=list(TOOL_NAMES),
            detail=f"Start with: python -m mcp_server (LLM {settings.llm_provider})",
        )
    except Exception as exc:  # noqa: BLE001
        return MCPHealthResponse(
            status="unavailable",
            server_name="ecommerce-ops",
            transport="stdio",
            tool_count=0,
            tools=[],
            detail=str(exc),
        )
