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
]
