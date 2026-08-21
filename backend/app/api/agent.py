"""Agent endpoints: start a run, list runs, inspect a run, stream its events."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.agent import runner
from app.api.deps import DbSession
from app.db.base import SessionLocal
from app.db.models import AgentRun, AgentStep, ApprovalStatus, RunStatus
from app.schemas.api import AgentRunRequest
from app.schemas.domain import AgentRunDetail, AgentRunListResult
from app.services import runs as run_service

router = APIRouter(prefix="/agent", tags=["agent"])

STREAM_POLL_SECONDS = 0.4
STREAM_MAX_SECONDS = 300


@router.post("/run", response_model=AgentRunDetail, status_code=status.HTTP_202_ACCEPTED)
def start_run(payload: AgentRunRequest, db: DbSession) -> AgentRunDetail:
    """Queue a run. Returns immediately - follow it via /agent/runs/{id}/events."""
    run = runner.create_run(db, payload.message, payload.session_id)
    runner.submit_run(run.id)
    db.expire_all()
    fresh = run_service.get_run(db, run.id)
    return run_service.to_detail(fresh or run)


@router.get("/runs", response_model=AgentRunListResult)
def list_runs(
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    run_status: str | None = Query(default=None, alias="status"),
) -> AgentRunListResult:
    rows = run_service.list_runs(db, limit=limit, offset=offset, status=run_status)
    return AgentRunListResult(
        count=len(rows), runs=[run_service.to_summary(r) for r in rows]
    )


@router.get("/runs/{run_id}", response_model=AgentRunDetail)
def get_run(run_id: int, db: DbSession) -> AgentRunDetail:
    run = run_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Agent run {run_id} not found")
    return run_service.to_detail(run)


@router.get("/runs/{run_id}/events")
def stream_events(run_id: int, db: DbSession) -> StreamingResponse:
    """Server-Sent Events for one run.

    The stream closes when the run finishes or pauses for approval; the client
    reopens it after resolving the approval.
    """
    if db.get(AgentRun, run_id) is None:
        raise HTTPException(status_code=404, detail=f"Agent run {run_id} not found")

    return StreamingResponse(
        _event_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _event_stream(run_id: int) -> Iterator[str]:
    """Polls the step table. Steps are committed as they happen, so this works
    across worker threads and would keep working across processes."""
    last_step_id = 0
    deadline = time.monotonic() + STREAM_MAX_SECONDS

    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            steps = (
                db.execute(
                    select(AgentStep)
                    .where(AgentStep.agent_run_id == run_id, AgentStep.id > last_step_id)
                    .order_by(AgentStep.id)
                )
                .scalars()
                .all()
            )
            for step in steps:
                last_step_id = step.id
                yield _sse(
                    "step",
                    {
                        "id": step.id,
                        "step_number": step.step_number,
                        "step_type": step.step_type,
                        "message": step.message,
                        "tool_name": step.tool_name,
                        "status": step.status,
                        "created_at": step.created_at,
                    },
                )

            run = db.get(AgentRun, run_id)
            if run is None:
                yield _sse("error", {"message": "run disappeared"})
                return

            if run.status == RunStatus.WAITING_FOR_APPROVAL.value:
                pending = [
                    a.id
                    for a in run.approvals
                    if a.status == ApprovalStatus.PENDING.value
                ]
                yield _sse(
                    "paused", {"run_id": run_id, "status": run.status, "approvals": pending}
                )
                return

            if run.status in RunStatus.terminal():
                yield _sse(
                    "done",
                    {
                        "run_id": run_id,
                        "status": run.status,
                        "final_response": run.final_response,
                        "error": run.error,
                        "duration_ms": run.duration_ms,
                    },
                )
                return
        finally:
            db.close()

        time.sleep(STREAM_POLL_SECONDS)

    yield _sse("timeout", {"run_id": run_id, "message": "event stream closed"})
