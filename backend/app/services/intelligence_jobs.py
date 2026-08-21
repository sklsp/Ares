"""Research job scheduling on the durable queue.

The API only enqueues; execution happens in the embedded worker (local
development default) or in standalone `python -m app.worker` processes
(production). Both claim jobs atomically from the same table.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ResearchJob, utcnow
from app.jobs import JobStatus
from app.jobs.queue import claim_next, due_jobs, enqueue, record_failure, transition
from app.logging_config import get_logger
from app.services.intelligence import run_investigation

logger = get_logger(__name__)

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None or _executor._shutdown:  # noqa: SLF001 - recreate after shutdown
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")
        return _executor


def shutdown() -> None:
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False, cancel_futures=True)
            _executor = None


def create_job(
    db: Session,
    objective: str,
    query: str,
    start_urls: list[str] | None = None,
    *,
    priority: int = 5,
) -> tuple[ResearchJob, bool]:
    """Enqueue a research job. Returns (job, created)."""
    job, created = enqueue(
        db, objective=objective, query=query, start_urls=start_urls,
        priority=priority,
    )
    if created and settings.embedded_worker:
        _get_executor().submit(_execute, job.id)
    return job, created


def cancel_job(db: Session, job: ResearchJob) -> ResearchJob:
    if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        transition(db, job, JobStatus.CANCELLED)
        db.refresh(job)
    return job


# -- embedded worker -----------------------------------------------------
def _execute(job_id: int) -> None:
    """Claim and run one job. Safe to run concurrently across processes."""
    db = SessionLocal()
    try:
        try:
            pending = due_jobs(db, limit=1)
        except Exception:  # noqa: BLE001 - schema gone (test teardown): give up quietly
            return
        if not any(j.id == job_id for j in pending):
            return  # claimed elsewhere, cancelled, or not yet due
        claimed = claim_next(db, worker_id=f"embedded:{id(_get_executor())}")
        if claimed is None or claimed.id != job_id:
            return
        try:
            from app.intelligence.crawler import CrawlPolicy, ResponsibleCrawler
            from app.observability.metrics import Timer, inc

            crawler = ResponsibleCrawler(CrawlPolicy(
                max_pages=20, max_depth=1, delay_seconds=0.5,
                allow_private_addresses=settings.crawler_allow_private_addresses,
            ))
            try:
                with Timer("research_job_duration_seconds"):
                    run_investigation(db, claimed, crawler=crawler)
            finally:
                crawler.close()
            transition(db, claimed, JobStatus.COMPLETED)
            inc("research_jobs_total", outcome="completed")
        except Exception as exc:  # noqa: BLE001 - record and requeue per policy
            from app.observability.metrics import inc

            inc("research_jobs_total", outcome="failed")
            try:
                logger.warning("Embedded worker failed job %s: %s", job_id, exc)
                transaction = db.get_transaction()
                if transaction is not None and transaction.is_active:
                    db.rollback()
            except Exception:  # noqa: BLE001 - DB may already be gone
                return
            fresh = db.get(ResearchJob, job_id)
            if fresh is not None:
                record_failure(db, fresh, str(exc))
    finally:
        db.close()


def recover_stale_jobs(db: Session) -> int:
    """Requeue RUNNING rows orphaned by a crashed API process at startup.

    Rows whose heartbeat/started_at is far in the past exceeded their
    recovery window and are marked FAILED instead of looping forever.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(minutes=30)
    stuck = list(
        db.execute(
            select(ResearchJob).where(
                ResearchJob.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value])
            )
        ).scalars().all()
    )
    recovered = 0
    for job in stuck:
        if job.status != JobStatus.RUNNING.value:
            continue
        started = job.started_at
        if started is not None:
            from app.db.models import as_utc

            if as_utc(started) < cutoff:
                job.status = JobStatus.FAILED.value
                job.stage = "failed"
                job.error = "Job was interrupted and exceeded its recovery window"
                job.completed_at = utcnow()
                continue
        # No live worker can hold it: the pool was in this process.
        job.status = JobStatus.QUEUED.value
        job.stage = "recovering after restart"
        job.worker_id = None
        recovered += 1
    if recovered or any(j.status == JobStatus.FAILED.value for j in stuck):
        db.commit()
    if recovered:
        logger.info("Recovered %d research job(s) after restart", recovered)
    if settings.embedded_worker:
        for job in stuck:
            if job.status == JobStatus.QUEUED.value:
                _get_executor().submit(_execute, job.id)
    return recovered
