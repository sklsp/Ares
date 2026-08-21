"""Persistent research job scheduling."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models import ResearchJob
from app.services.intelligence import run_investigation

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")


def create_job(db: Session, objective: str, query: str, start_urls: list[str] | None = None) -> ResearchJob:
    job = ResearchJob(objective=objective, query=query, stats={"domains_discovered": 0, "pages_discovered": 0, "pages_crawled": 0, "products_discovered": 0, "opportunities_found": 0})
    db.add(job); db.commit(); db.refresh(job)
    _executor.submit(_run, job.id, start_urls or [])
    return job


def _run(job_id: int, start_urls: list[str]) -> None:
    db = SessionLocal()
    try:
        job = db.get(ResearchJob, job_id)
        if job:
            run_investigation(db, job, start_urls=start_urls or None)
    except Exception:
        db.rollback()
    finally:
        db.close()


def cancel_job(db: Session, job: ResearchJob) -> ResearchJob:
    if job.status in {"QUEUED", "RUNNING"}:
        job.status = "CANCELLED"; job.stage = "cancelled"; db.commit(); db.refresh(job)
    return job
