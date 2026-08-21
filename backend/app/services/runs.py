"""Read helpers for agent runs, shared by the REST API and the agent tools."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import AgentRun, ApprovalStatus, StepType
from app.schemas.domain import (
    AgentRunDetail,
    AgentRunSummary,
    AgentStepOut,
    ApprovalOut,
)


def _run_query():
    return select(AgentRun).options(
        selectinload(AgentRun.steps), selectinload(AgentRun.approvals)
    )


def get_run(db: Session, run_id: int) -> AgentRun | None:
    return db.execute(_run_query().where(AgentRun.id == run_id)).scalar_one_or_none()


def list_runs(
    db: Session, *, limit: int = 20, offset: int = 0, status: str | None = None
) -> list[AgentRun]:
    stmt = _run_query().order_by(AgentRun.id.desc()).limit(limit).offset(offset)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    return list(db.execute(stmt).scalars().all())


def to_summary(run: AgentRun) -> AgentRunSummary:
    return AgentRunSummary(
        id=run.id,
        session_id=run.session_id,
        user_request=run.user_request,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_ms=run.duration_ms,
        final_response=run.final_response,
        error=run.error,
        tools_used=tools_used(run),
        step_count=len(run.steps),
        pending_approvals=sum(
            1 for a in run.approvals if a.status == ApprovalStatus.PENDING.value
        ),
    )


def to_detail(run: AgentRun) -> AgentRunDetail:
    return AgentRunDetail(
        **to_summary(run).model_dump(),
        steps=[AgentStepOut.model_validate(s) for s in run.steps],
        approvals=[ApprovalOut.model_validate(a) for a in run.approvals],
    )


def tools_used(run: AgentRun) -> list[str]:
    """Distinct tool names in call order."""
    seen: list[str] = []
    for step in run.steps:
        if step.step_type == StepType.TOOL_CALL.value and step.tool_name:
            if step.tool_name not in seen:
                seen.append(step.tool_name)
    return seen
