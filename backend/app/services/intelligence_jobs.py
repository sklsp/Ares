"""Persistent research job scheduling with crash recovery.

Jobs are durable rows. If the process dies mid-run, a job can be left in
RUNNING forever; `recover_stale_jobs` requeues those rows on startup so a
restart heals interrupted work instead of leaking stuck state.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ResearchJob, ResearchJobStatus, as_utc, utcnow
from app.logging_config import get_logger
from app.services.intelligence import run_investigation

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")
_inflight: set[int] = set()

# A RUNNING row older than this is considered orphaned by a crashed worker.
STALE_RUNNING_THRESHOLD = timedelta(minutes=30)


def create_job(db: Session, objective: str, query: str, start_urls: list[str] | None = None) -> ResearchJob:
    job = ResearchJob(objective=objective, query=query, stats={"domains_discovered": 0, "pages_discovered": 0, "pages_crawled": 0, "products_discovered": 0, "opportunities_found": 0})
    db.add(job); db.commit(); db.refresh(job)
    _submit(job.id, start_urls or [])
    return job


def _submit(job_id: int, start_urls: list[str]) -> None:
    """Track in-flight jobs so shutdown can drain them deterministically."""
    _inflight.add(job_id)

    def _done(_future) -> None:
        _inflight.discard(job_id)

    _executor.submit(_run, job_id, start_urls).add_done_callback(_done)


def _run(job_id: int, start_urls: list[str]) -> None:
    db = SessionLocal()
    try:
        job = db.get(ResearchJob, job_id)
        if job is None or job.status == ResearchJobStatus.CANCELLED.value:
            return
        run_investigation(db, job, start_urls=start_urls or None)
    except Exception:  # noqa: BLE001 - a worker must never die silently
        logger.exception("Research worker failed for job %s", job_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - rollback must not mask the original failure
            logger.exception("Rollback failed for job %s", job_id)
    finally:
        db.close()


def recover_stale_jobs(db: Session) -> int:
    """Requeue jobs orphaned by a crash and fail permanently-stuck ones.

    Called at API startup. QUEUED/RUNNING rows found after a restart cannot
    have a live worker (the pool is in-process), so they are resubmitted once;
    if a recovered row goes stale again it is marked FAILED instead of looping.
    """
    cutoff = utcnow() - STALE_RUNNING_THRESHOLD
    stuck = list(
        db.execute(
            select(ResearchJob).where(
                ResearchJob.status.in_(
                    [ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value]
                )
            )
        ).scalars().all()
    )
    recovered = 0
    for job in stuck:
        started = as_utc(job.started_at) if job.started_at is not None else None
        if started is not None and started < cutoff:
            # Was already running before an earlier recovery attempt: give up.
            job.status = ResearchJobStatus.FAILED.value
            job.stage = "failed"
            job.error = "Job was interrupted and exceeded its recovery window"
            job.completed_at = utcnow()
            logger.warning("Marked stale research job %s as FAILED", job.id)
            continue
        job.status = ResearchJobStatus.QUEUED.value
        job.stage = "recovering after restart"
        job.error = None
        recovered += 1
        _submit(job.id, [])
    db.commit()
    if recovered:
        logger.info("Recovered %d research job(s) after restart", recovered)
    return recovered


def cancel_job(db: Session, job: ResearchJob) -> ResearchJob:
    if job.status in {ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value}:
        job.status = ResearchJobStatus.CANCELLED.value
        job.stage = "cancelled"
        db.commit(); db.refresh(job)
    return job


def wait_for_jobs(timeout: float | None = None) -> bool:
    """Block until no research jobs are in flight. Used by tests and shutdown."""
    import time

    deadline = None if timeout is None else time.monotonic() + timeout
    while _inflight:
        if deadline is not None and time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True
