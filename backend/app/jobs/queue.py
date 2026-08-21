"""Durable, multi-process job queue backed by the application database.

Why the database instead of Redis: the stack already guarantees durable
rows, migrations and transactions. A `SELECT ... ` claim guarded by an
UPDATE ... WHERE status='QUEUED' gives atomic cross-process claiming on
both SQLite (serialized writes) and PostgreSQL (row locks) without new
infrastructure. Redis/Arq can be swapped in behind this same interface
later without touching callers.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ResearchJob, utcnow
from app.jobs import InvalidTransition, JobStatus, can_transition

# Retry policy for research jobs.
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (30, 120)


def job_signature(objective: str, query: str, start_urls: list[str]) -> str:
    """Deterministic idempotency signature for a research request."""
    payload = json.dumps(
        {"objective": objective.strip().lower(), "query": query.strip().lower(),
         "urls": sorted(u.rstrip("/") for u in start_urls)},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def enqueue(
    db: Session,
    *,
    objective: str,
    query: str,
    start_urls: list[str] | None = None,
    priority: int = 5,
    idempotency_key: str | None = None,
    organization_id: int | None = None,
) -> tuple[ResearchJob, bool]:
    """Create a research job. Returns (job, created).

    With an idempotency key, a duplicate submission returns the existing
    QUEUED/RUNNING job instead of creating a second one.
    """
    # Idempotency is per-tenant: two organizations researching the same
    # niche are independent investigations.
    scope_prefix = f"org{organization_id}:" if organization_id else ""
    signature = idempotency_key or f"{scope_prefix}{job_signature(objective, query, start_urls or [])}"
    if idempotency_key is None and signature:
        existing = db.execute(
            select(ResearchJob)
            .where(ResearchJob.idempotency_key == signature)
            .where(ResearchJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
            .order_by(ResearchJob.id.desc())
        ).scalars().first()
        if existing is not None:
            return existing, False

    job = ResearchJob(
        objective=objective,
        query=query,
        organization_id=organization_id,
        priority=priority,
        idempotency_key=signature,
        stats={"domains_discovered": 0, "pages_discovered": 0, "pages_crawled": 0,
               "products_discovered": 0, "opportunities_found": 0},
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent process enqueued the same signature first: return its row
        # only if it is still active; otherwise allow a fresh submission.
        db.rollback()
        existing = db.execute(
            select(ResearchJob)
            .where(ResearchJob.idempotency_key == signature)
            .where(ResearchJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]))
        ).scalars().first()
        if existing is not None:
            return existing, False
        # Terminal row holds the key; clear it so the new submission can proceed.
        stale = db.execute(
            select(ResearchJob).where(ResearchJob.idempotency_key == signature)
        ).scalars().first()
        if stale is not None:
            stale.idempotency_key = None
            db.commit()
        db.add(job)
        db.commit()
        db.refresh(job)
        return job, True
    db.refresh(job)
    return job, True


def claim_next(db: Session, worker_id: str) -> ResearchJob | None:
    """Atomically claim the highest-priority queued job for this worker.

    The UPDATE ... WHERE status=QUEUED guard means a concurrent worker's
    claim fails harmlessly and it simply picks the next row.
    """
    candidates = db.execute(
        select(ResearchJob)
        .where(ResearchJob.status == JobStatus.QUEUED.value)
        .order_by(ResearchJob.priority.asc(), ResearchJob.created_at.asc())
        .limit(10)
    ).scalars().all()
    for job in candidates:
        updated = db.execute(
            update_job_status_stmt(job.id, JobStatus.RUNNING, JobStatus.QUEUED,
                                   worker_id=worker_id)
        )
        db.commit()
        if updated.rowcount:
            db.refresh(job)
            return job
    return None


def update_job_status_stmt(job_id: int, target: JobStatus, expected: JobStatus,
                           worker_id: str | None = None):
    from sqlalchemy import update

    values: dict[str, Any] = {"status": target.value}
    if target is JobStatus.RUNNING:
        values["started_at"] = utcnow()
    elif target in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        values["completed_at"] = utcnow()
    if worker_id:
        values["worker_id"] = worker_id
    return (
        update(ResearchJob)
        .where(ResearchJob.id == job_id)
        .where(ResearchJob.status == expected.value)
        .values(**values)
    )


def transition(db: Session, job: ResearchJob, target: JobStatus) -> ResearchJob:
    """Apply a validated state transition to a loaded job."""
    current = JobStatus(job.status)
    if not can_transition(current, target):
        raise InvalidTransition(f"{current.value} -> {target.value} is not allowed")
    job.status = target.value
    if target is JobStatus.RUNNING and job.started_at is None:
        job.started_at = utcnow()
    if target in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
        job.completed_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def record_failure(db: Session, job: ResearchJob, error: str) -> ResearchJob:
    """Mark a failure, requeueing with backoff when attempts remain."""
    job.retry_count += 1
    job.error = error[:2000]
    attempts_used = job.retry_count
    if attempts_used < MAX_ATTEMPTS:
        # Requeue; the worker's poll delay provides the backoff window.
        job.status = JobStatus.QUEUED.value
        job.stage = f"retry {attempts_used} scheduled"
        delay = RETRY_DELAYS_SECONDS[min(attempts_used - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        job.run_after = utcnow() + __import__("datetime").timedelta(seconds=delay)
    else:
        job.status = JobStatus.FAILED.value
        job.stage = "failed"
        job.completed_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def queue_depth(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(ResearchJob.status, func.count(ResearchJob.id)).group_by(ResearchJob.status)
    ).all()
    return {status: int(count) for status, count in rows}


def due_jobs(db: Session, limit: int = 10) -> list[ResearchJob]:
    now: datetime = utcnow()
    stmt = (
        select(ResearchJob)
        .where(ResearchJob.status == JobStatus.QUEUED.value)
        .order_by(ResearchJob.priority.asc(), ResearchJob.created_at.asc())
        .limit(limit)
    )
    jobs = list(db.execute(stmt).scalars().all())
    return [j for j in jobs if j.run_after is None or as_utc(j.run_after) <= now]


def as_utc(value: datetime) -> datetime:
    from app.db.models import as_utc as model_as_utc

    return model_as_utc(value)
