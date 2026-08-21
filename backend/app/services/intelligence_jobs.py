"""Research job scheduling.

The database row is the source of truth for business state; the queue
transport (Redis in production, inline in tests/local dev) is only the
execution mechanism. The API creates the durable record, enqueues, and
returns immediately — workers do the rest.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import SessionLocal
from app.db.models import ResearchJob, utcnow
from app.jobs import JobStatus
from app.jobs.queue import enqueue as db_enqueue
from app.jobs.queue import record_failure, transition
from app.jobs.transport import get_backend, register_handler
from app.logging_config import get_logger
from app.observability.metrics import Timer, inc
from app.observability.tracing import traced

logger = get_logger(__name__)

JOB_TYPE = "research"
# A job parked in processing by a dead worker is reclaimable after this long.
STALE_PROCESSING_SECONDS = 300.0


def create_job(
    db: Session,
    objective: str,
    query: str,
    start_urls: list[str] | None = None,
    *,
    priority: int = 5,
    organization_id: int | None = None,
) -> tuple[ResearchJob, bool]:
    """Create the durable record and enqueue for execution."""
    job, created = db_enqueue(
        db, objective=objective, query=query, start_urls=start_urls,
        priority=priority, organization_id=organization_id,
    )
    if created:
        try:
            with traced("research.enqueue", {"job.id": job.id,
                                             "organization.id": organization_id}):
                get_backend().enqueue(JOB_TYPE, {"job_id": job.id}, job_id=f"job-{job.id}")
            inc("jobs_enqueued_total", type=JOB_TYPE)
        except Exception:  # noqa: BLE001 - queue outage must not lose the record
            logger.exception("Queue unavailable; job %s stays QUEUED for recovery", job.id)
            inc("jobs_enqueue_failed_total", type=JOB_TYPE)
            if not settings.embedded_worker:
                # Without an embedded poller nothing would pick it up: surface it.
                raise RuntimeError(
                    "Job saved but the queue is unavailable; it will run when the queue recovers"
                ) from None
    return job, created


def cancel_job(db: Session, job: ResearchJob) -> ResearchJob:
    if job.status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
        transition(db, job, JobStatus.CANCELLED)
        db.refresh(job)
    return job


def execute_job(job_id: int) -> None:
    """Run one research job by id. Idempotent: safe on duplicate delivery.

    Guards against double execution because the state machine rejects
    QUEUED -> RUNNING transitions from a non-QUEUED row atomically.
    """
    from app.intelligence.crawler import CrawlPolicy, ResponsibleCrawler

    db = SessionLocal()
    try:
        job = db.get(ResearchJob, job_id)
        if job is None or job.status != JobStatus.QUEUED.value:
            return  # cancelled, already running/completed: duplicate delivery no-op
        claimed = claim_job_row(db, job.id)
        if claimed is None:
            return
        try:
            crawler = ResponsibleCrawler(CrawlPolicy(
                max_pages=20, max_depth=1, delay_seconds=0.5,
                allow_private_addresses=settings.crawler_allow_private_addresses,
            ))
            try:
                with traced("research.execute", {"job.id": job_id}), \
                     Timer("research_job_duration_seconds"):
                    from app.services.intelligence import run_investigation

                    run_investigation(db, claimed, crawler=crawler)
            finally:
                crawler.close()
            transition(db, claimed, JobStatus.COMPLETED)
            inc("research_jobs_total", outcome="completed")
        except Exception as exc:  # noqa: BLE001 - failures must be recorded
            logger.warning("Research job %s failed: %s", job_id, exc)
            inc("research_jobs_total", outcome="failed")
            db.rollback()
            fresh = db.get(ResearchJob, job_id)
            if fresh is not None:
                record_failure(db, fresh, str(exc))
    finally:
        db.close()


def claim_job_row(db: Session, job_id: int):
    """Atomically move a QUEUED row to RUNNING via the guarded UPDATE."""
    from app.jobs.queue import claim_next

    return claim_next(db, worker_id=f"queue:{job_id}")


def recover_stale_jobs(db: Session) -> int:
    """Requeue RUNNING rows orphaned by a crashed API/worker at startup."""
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
            from datetime import timedelta

            from app.db.models import as_utc

            if as_utc(started) < utcnow() - timedelta(minutes=30):
                job.status = JobStatus.FAILED.value
                job.stage = "failed"
                job.error = "Job was interrupted and exceeded its recovery window"
                job.completed_at = utcnow()
                continue
        job.status = JobStatus.QUEUED.value
        job.stage = "recovering after restart"
        job.worker_id = None
        recovered += 1
    if recovered or any(j.status == JobStatus.FAILED.value for j in stuck):
        db.commit()
    if recovered:
        logger.info("Recovered %d research job(s) after restart", recovered)
        # Re-deliver through the transport so any worker picks them up.
        for job in stuck:
            if job.status == JobStatus.QUEUED.value:
                try:
                    get_backend().enqueue(JOB_TYPE, {"job_id": job.id},
                                          job_id=f"job-{job.id}-retry")
                except Exception:  # noqa: BLE001 - queue down: DB recovery persists
                    logger.warning("Could not re-enqueue recovered job %s", job.id)
    return recovered


def _handle_research(payload: dict) -> None:
    """Transport handler: executes one queued research job."""
    execute_job(int(payload["job_id"]))


register_handler(JOB_TYPE, _handle_research)


def shutdown() -> None:
    """Retained for API lifecycle symmetry; the ThreadPool path is gone."""
    return None
