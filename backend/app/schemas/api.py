"""Request bodies and API-only response envelopes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="What to ask the agent")
    session_id: str = Field(default="default", max_length=64)


class ApprovalDecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class ProductUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=8000)
    price: float | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    status: str | None = Field(default=None, pattern="^(active|draft|archived)$")

    def changed_fields(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
    llm: dict[str, Any]


class MCPHealthResponse(BaseModel):
    status: str
    server_name: str
    transport: str
    tool_count: int
    tools: list[str]
    detail: str | None = None


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
