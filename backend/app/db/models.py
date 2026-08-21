"""SQLAlchemy models.

Six tables: the store (products, inventory, orders) and the agent audit
trail (agent_runs, agent_steps, approval_requests).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; normalise before arithmetic."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @classmethod
    def terminal(cls) -> set[str]:
        return {cls.COMPLETED.value, cls.FAILED.value}


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class StepType(StrEnum):
    REQUEST = "request"
    DECISION = "decision"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESOLVED = "approval_resolved"
    FINAL = "final"
    ERROR = "error"


class StepStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    PENDING = "pending"


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    inventory: Mapped["Inventory | None"] = relationship(
        back_populates="product", uselist=False, cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    @property
    def inventory_quantity(self) -> int:
        return self.inventory.quantity if self.inventory else 0


class Inventory(Base):
    """Stock levels, kept out of the catalog table so reorder policy can
    evolve (and later be per warehouse) without migrating products."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), unique=True, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, default=10)
    warehouse: Mapped[str] = mapped_column(String(40), default="MAIN")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    product: Mapped[Product] = relationship(back_populates="inventory")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    product: Mapped[Product] = relationship(back_populates="orders")


# --------------------------------------------------------------------------
# Agent audit trail
# --------------------------------------------------------------------------
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Tenant scope: derived server-side from authenticated identity, never
    # trusted from client input. NULL only for legacy rows created before
    # multi-tenancy; those remain visible to all tenants until migrated.
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    user_request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), default=RunStatus.RUNNING.value, index=True
    )
    final_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Serialized conversation, so a run paused for approval can be resumed by
    # any worker (or after a restart) instead of living in process memory.
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    # Tool calls from the current batch that have not been processed yet.
    pending_tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, default=list
    )
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls_made: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentStep.step_number",
    )
    approvals: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ApprovalRequest.id",
    )

    @property
    def duration_ms(self) -> int | None:
        if self.completed_at is None:
            return None
        delta = as_utc(self.completed_at) - as_utc(self.started_at)
        return int(delta.total_seconds() * 1000)


class AgentStep(Base):
    """One observable event in a run. This is what the activity feed renders."""

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    step_number: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    output: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=StepStatus.OK.value)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


Index("ix_agent_steps_run_number", AgentStep.agent_run_id, AgentStep.step_number)


class ApprovalRequest(Base):
    """A write tool call the agent wants to perform, held until a human decides."""

    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # Human readable before/after diff rendered by the approvals UI.
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(16), default=ApprovalStatus.PENDING.value, index=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[Any | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[AgentRun] = relationship(back_populates="approvals")


# --------------------------------------------------------------------------
# Market intelligence
# --------------------------------------------------------------------------
class ResearchJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OpportunityStatus(StrEnum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    DISMISSED = "DISMISSED"
    ACTIONED = "ACTIONED"


class ExternalStore(Base):
    __tablename__ = "external_stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    niche: Mapped[str] = mapped_column(String(160), default="")
    platform: Mapped[str] = mapped_column(String(40), default="unknown")
    country: Mapped[str] = mapped_column(String(8), default="")
    product_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    store_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONType, default=dict
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crawl_status: Mapped[str] = mapped_column(String(24), default="discovered")

    products: Mapped[list["ExternalProduct"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class ExternalProduct(Base):
    __tablename__ = "external_products"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("external_stores.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    brand: Mapped[str] = mapped_column(String(160), default="")
    category: Mapped[str] = mapped_column(String(160), default="")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="")
    availability: Mapped[str] = mapped_column(String(40), default="unknown")
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    store: Mapped[ExternalStore] = relationship(back_populates="products")
    snapshots: Mapped[list["ProductSnapshot"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_product_id: Mapped[int] = mapped_column(
        ForeignKey("external_products.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    availability: Mapped[str] = mapped_column(String(40), default="unknown")
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    product: Mapped[ExternalProduct] = relationship(back_populates="snapshots")


class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    objective: Mapped[str] = mapped_column(Text)
    query: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(24), default=ResearchJobStatus.QUEUED.value, index=True)
    stage: Mapped[str] = mapped_column(String(80), default="queued")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    # Durable queue fields (multi-process workers).
    priority: Mapped[int] = mapped_column(Integer, default=5, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    opportunities: Mapped[list["Opportunity"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    research_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    recommended_action: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_urls: Mapped[list[str]] = mapped_column(JSONType, default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    competition_level: Mapped[str] = mapped_column(String(24), default="unknown")
    demand_signals: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(24), default=OpportunityStatus.NEW.value, index=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[ResearchJob | None] = relationship(back_populates="opportunities")


class OpportunityEvidence(Base):
    __tablename__ = "opportunity_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True
    )
    source_url: Mapped[str] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(255), index=True)
    claim: Mapped[str] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(80), default="structured")
    observed_value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = [
    "AgentRun",
    "AgentStep",
    "ApprovalRequest",
    "ApprovalStatus",
    "Inventory",
    "Order",
    "Product",
    "RunStatus",
    "StepStatus",
    "StepType",
    "as_utc",
    "utcnow",
    "ExternalStore",
    "ExternalProduct",
    "ProductSnapshot",
    "ResearchJob",
    "ResearchJobStatus",
    "Opportunity",
    "OpportunityEvidence",
    "OpportunityStatus",
]
