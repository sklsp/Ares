"""Behavioural tests for durable research-job orchestration.

Covers the branches of app/services/intelligence_jobs.py that the API-level
suite does not reach: a queue outage at creation time (the durable row must
survive and only surface an error when no embedded worker can pick it up),
duplicate delivery to a vanished job, losing the atomic claim race, successful
completion, recover_stale_jobs skipping QUEUED rows while requeueing RUNNING
orphans, re-enqueue failure during recovery, and the shutdown() lifecycle hook.

No network is used: run_investigation is monkeypatched where a full crawl would
be needed (permitted by the test rules), and the transport backend is replaced
with recording or broken fakes so queue outages are deterministic instead of
depending on the inline backend executing jobs synchronously.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import ResearchJob, utcnow
from app.jobs.queue import JobStatus
from app.services.intelligence_jobs import (
    create_job,
    execute_job,
    recover_stale_jobs,
    shutdown,
)


class _BrokenBackend:
    """A transport whose enqueue always fails (queue outage)."""

    def enqueue(self, *args, **kwargs):
        raise RuntimeError("queue down")


class _RecordingBackend:
    """A transport that records deliveries without executing them."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, dict, str | None]] = []

    def enqueue(self, job_type, payload, *, job_id=None):
        self.enqueued.append((job_type, payload, job_id))


def _job(db, **overrides):
    fields = dict(organization_id=1, objective="Find roller stores", query="roller")
    fields.update(overrides)
    job = ResearchJob(**fields)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _patch_investigation(monkeypatch):
    calls: list[int] = []

    def fake(db, job, *, start_urls=None, search=None, crawler=None):
        calls.append(job.id)
        return {"pages": 1}

    monkeypatch.setattr("app.services.intelligence.run_investigation", fake)
    return calls


def test_create_job_keeps_row_when_enqueue_fails(db, monkeypatch):
    """A queue outage must not lose the durable row; with no embedded worker it surfaces."""
    monkeypatch.setattr(
        "app.services.intelligence_jobs.get_backend", lambda: _BrokenBackend()
    )

    with pytest.raises(RuntimeError, match="queue is unavailable"):
        create_job(db, "Find roller stores", "roller")

    row = db.execute(select(ResearchJob)).scalar_one()
    assert row.status == JobStatus.QUEUED.value
    assert row.query == "roller"


def test_execute_job_ignores_delivery_for_vanished_job(db, monkeypatch):
    """A duplicate delivery for a job that no longer exists is a silent no-op."""
    calls = _patch_investigation(monkeypatch)

    execute_job(999999)

    assert calls == []


def test_execute_job_returns_when_claim_race_lost(db, monkeypatch):
    """If another worker wins the atomic claim, this delivery does nothing."""
    calls = _patch_investigation(monkeypatch)
    job = _job(db)
    monkeypatch.setattr("app.jobs.queue.claim_next", lambda db, worker_id: None)

    execute_job(job.id)

    assert calls == []
    db.refresh(job)
    assert job.status == JobStatus.QUEUED.value


def test_execute_job_completes_claimed_job(db, monkeypatch):
    """A claimed QUEUED job runs the investigation and lands in COMPLETED."""
    calls = _patch_investigation(monkeypatch)
    job = _job(db)

    execute_job(job.id)

    assert calls == [job.id]
    db.refresh(job)
    assert job.status == JobStatus.COMPLETED.value
    assert job.completed_at is not None
    assert job.worker_id == f"queue:{job.id}"


def test_recover_stale_jobs_skips_queued_rows(db, monkeypatch):
    """QUEUED rows are not stale: only RUNNING orphans get requeued and redelivered."""
    backend = _RecordingBackend()
    monkeypatch.setattr("app.services.intelligence_jobs.get_backend", lambda: backend)
    queued = _job(db, priority=9)
    stuck = _job(
        db, status=JobStatus.RUNNING.value, worker_id="dead-worker",
        started_at=utcnow(), priority=5,
    )

    recovered = recover_stale_jobs(db)

    assert recovered == 1
    db.expire_all()
    q = db.get(ResearchJob, queued.id)
    s = db.get(ResearchJob, stuck.id)
    # The pre-existing QUEUED row is untouched by recovery.
    assert q.status == JobStatus.QUEUED.value and q.worker_id is None
    # The RUNNING orphan was requeued for a worker to pick up again.
    assert s.status == JobStatus.QUEUED.value
    assert s.stage == "recovering after restart"
    assert s.worker_id is None
    # And it was re-delivered through the transport.
    assert ("research", {"job_id": stuck.id}, f"job-{stuck.id}-retry") in backend.enqueued


def test_recover_stale_jobs_persists_when_reenqueue_fails(db, monkeypatch):
    """If the queue is down during recovery, the DB requeue still persists."""
    monkeypatch.setattr(
        "app.services.intelligence_jobs.get_backend", lambda: _BrokenBackend()
    )
    stuck = _job(
        db, status=JobStatus.RUNNING.value, worker_id="dead-worker",
        started_at=utcnow(),
    )

    recovered = recover_stale_jobs(db)

    assert recovered == 1
    db.expire_all()
    s = db.get(ResearchJob, stuck.id)
    assert s.status == JobStatus.QUEUED.value
    assert s.stage == "recovering after restart"
    assert s.worker_id is None


def test_shutdown_is_noop():
    """shutdown() exists for API lifecycle symmetry and must be harmless."""
    assert shutdown() is None
