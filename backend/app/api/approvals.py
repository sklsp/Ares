"""Human-in-the-loop approval endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.agent import runner
from app.api.deps import DbSession
from app.db.models import ApprovalStatus
from app.schemas.api import ApprovalDecisionRequest
from app.schemas.domain import ApprovalOut
from app.services import approvals as approval_service

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    db: DbSession,
    status: str = Query(default=ApprovalStatus.PENDING.value),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ApprovalOut]:
    """Pending approvals by default. Pass status=all for the full history."""
    wanted = None if status.lower() == "all" else status.upper()
    rows = approval_service.list_approvals(db, status=wanted, limit=limit)
    return [ApprovalOut.model_validate(r) for r in rows]


@router.get("/{approval_id}", response_model=ApprovalOut)
def get_approval(approval_id: int, db: DbSession) -> ApprovalOut:
    approval = approval_service.get(db, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return ApprovalOut.model_validate(approval)


def _decide(
    approval_id: int, db: DbSession, approved: bool, note: str | None
) -> ApprovalOut:
    approval = approval_service.get(db, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    try:
        approval_service.resolve(db, approval, approved=approved, note=note)
    except approval_service.AlreadyResolvedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # The engine performs the mutation on a worker; this endpoint never does.
    runner.submit_resume(approval.agent_run_id)
    db.refresh(approval)
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
def approve(
    approval_id: int, db: DbSession, payload: ApprovalDecisionRequest | None = None
) -> ApprovalOut:
    return _decide(approval_id, db, True, payload.note if payload else None)


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
def reject(
    approval_id: int, db: DbSession, payload: ApprovalDecisionRequest | None = None
) -> ApprovalOut:
    return _decide(approval_id, db, False, payload.note if payload else None)
