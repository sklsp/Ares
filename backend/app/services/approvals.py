"""Approval resolution.

The only place an approval changes state. Mutations still do not run here -
resolving an approval just unblocks the run, and the engine executes the tool
on its own worker.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApprovalRequest, ApprovalStatus, utcnow
from app.logging_config import get_logger

logger = get_logger(__name__)


class ApprovalError(RuntimeError):
    pass


class AlreadyResolvedError(ApprovalError):
    pass


def get(db: Session, approval_id: int) -> ApprovalRequest | None:
    return db.get(ApprovalRequest, approval_id)


def list_approvals(
    db: Session, *, status: str | None = None, limit: int = 50
) -> list[ApprovalRequest]:
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    return list(db.execute(stmt).scalars().all())


def resolve(
    db: Session, approval: ApprovalRequest, *, approved: bool, note: str | None = None
) -> ApprovalRequest:
    if approval.status != ApprovalStatus.PENDING.value:
        raise AlreadyResolvedError(
            f"Approval {approval.id} was already {approval.status.lower()}"
        )

    approval.status = (
        ApprovalStatus.APPROVED.value if approved else ApprovalStatus.REJECTED.value
    )
    approval.decision_note = note
    approval.resolved_at = utcnow()
    db.commit()
    db.refresh(approval)
    logger.info(
        "Approval %s: id=%s run=%s tool=%s",
        approval.status.lower(),
        approval.id,
        approval.agent_run_id,
        approval.tool_name,
    )
    return approval
