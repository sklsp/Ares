"""Durable queue semantics: idempotency, transitions, concurrent claiming."""

from __future__ import annotations

import threading

import pytest

from app.db.models import ResearchJob
from app.jobs import InvalidTransition, JobStatus
from app.jobs.queue import (
    claim_next,
    enqueue,
    job_signature,
    transition,
)


def _make(db, **kwargs) -> ResearchJob:
    job, _created = enqueue(db, objective=kwargs.get("objective", "Investigate"),
                            query=kwargs.get("query", "fitness"),
                            start_urls=kwargs.get("start_urls"))
    return job


# --- idempotency --------------------------------------------------------
def test_duplicate_submission_returns_existing_job(db):
    first, created1 = enqueue(db, objective="Find fitness products",
                              query="fitness", start_urls=["https://a.example"])
    second, created2 = enqueue(db, objective="Find fitness products",
                               query="fitness", start_urls=["https://a.example/"])
    assert created1 is True
    assert created2 is False
    assert first.id == second.id


def test_completed_jobs_are_not_idempotency_blocks(db):
    first, _ = enqueue(db, objective="Scan", query="fitness")
    transition(db, first, JobStatus.RUNNING)
    transition(db, first, JobStatus.COMPLETED)
    second, created = enqueue(db, objective="Scan", query="fitness")
    assert created is True
    assert second.id != first.id


def test_signature_is_order_insensitive_for_urls(db):
    a = job_signature("obj", "q", ["https://a.example", "https://b.example"])
    b = job_signature("obj", "q", ["https://b.example", "https://a.example"])
    assert a == b


# --- state machine ------------------------------------------------------
def test_invalid_transitions_are_rejected(db):
    job = _make(db)
    transition(db, job, JobStatus.RUNNING)
    transition(db, job, JobStatus.COMPLETED)
    with pytest.raises(InvalidTransition):
        transition(db, job, JobStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        transition(db, job, JobStatus.FAILED)


def test_cancelled_job_cannot_run_again(db):
    job = _make(db)
    transition(db, job, JobStatus.CANCELLED)
    with pytest.raises(InvalidTransition):
        transition(db, job, JobStatus.RUNNING)


# --- concurrent claiming ------------------------------------------------
def test_two_workers_never_claim_the_same_job(db):
    jobs = [
        _make(db, query=f"niche-{index}") for index in range(4)
    ]
    claimed_by: dict[int, str] = {}
    collisions: list[str] = []
    lock = threading.Lock()

    def work(worker_id: str) -> None:
        # Each worker uses its own session, as in production.
        from app.db.base import SessionLocal

        session = SessionLocal()
        try:
            while True:
                job = claim_next(session, worker_id)
                if job is None:
                    return
                with lock:
                    if job.id in claimed_by:
                        collisions.append(job.id)
                    claimed_by[job.id] = worker_id
        finally:
            session.close()

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert collisions == []
    assert len(claimed_by) == len(jobs)
