"""API DTOs for market intelligence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ResearchJobRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=500)
    query: str = Field(min_length=2, max_length=255)
    start_urls: list[str] = Field(default_factory=list, max_length=20)


class ResearchJobOut(BaseModel):
    id: int
    objective: str
    query: str
    status: str
    stage: str
    stats: dict[str, Any]
    error: str | None = None
    result: dict[str, Any] | None = None
    priority: int = 5
    worker_id: str | None = None
    retry_count: int = 0
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class OpportunityOut(BaseModel):
    id: int
    research_job_id: int | None
    type: str
    title: str
    summary: str
    recommended_action: str
    evidence: dict[str, Any]
    source_urls: list[str]
    score: float
    confidence: float
    competition_level: str
    demand_signals: dict[str, Any]
    status: str
    discovered_at: datetime
    last_verified_at: datetime

    model_config = {"from_attributes": True}
