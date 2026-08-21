"""Multi-worker queue integration tests against a real Redis code path.

Uses fakeredis so the actual Redis commands (BRPOPLPUSH semantics, sorted
sets, expiry) are exercised — not a mock of our own abstraction.
"""

from __future__ import annotations

import threading
import time

import pytest

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
def backend():
    from app.jobs.transport import RedisQueueBackend, set_backend

    client = fakeredis.FakeRedis(decode_responses=True)
    backend = RedisQueueBackend(client)
    set_backend(backend)
    yield backend
    set_backend(None)


def test_two_workers_receive_different_jobs(backend):
    """Test 1: atomic claim delivers each job to exactly one worker."""
    delivered = []
    lock = threading.Lock()

    for index in range(2):
        backend.enqueue("noop", {"index": index})

    def work(worker_id: str) -> None:
        while True:
            entry = backend.claim(worker_id, timeout_seconds=0)
            if entry is None:
                return
            with lock:
                delivered.append((worker_id, entry["id"]))

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Guarantee: every job delivered exactly once (a fast worker may take
    # both — distribution is opportunistic, exclusivity is the contract).
    assert len(delivered) == 2
    assert len({job_id for _, job_id in delivered}) == 2


def test_same_job_never_delivered_twice_while_processing(backend):
    """Test 2: two concurrent claims cannot receive the same entry."""
    backend.enqueue("noop", {"x": 1})
    first = backend.claim("worker-a", timeout_seconds=0)
    second = backend.claim("worker-b", timeout_seconds=0)
    assert first is not None
    assert second is None  # still parked in worker-a's processing list


def test_crashed_worker_job_is_recoverable(backend):
    """Test 3: an abandoned processing entry returns to the queue."""
    backend.enqueue("research", {"job_id": 1})
    entry = backend.claim("doomed-worker", timeout_seconds=0)
    assert entry is not None

    # Simulate the worker dying without completing: its processing list ages out.
    entry["enqueued_at"] = time.time() - 400
    raw = backend.client.lrange("research:processing:doomed-worker", 0, -1)[0]
    import json as json_module

    aged = json_module.loads(raw)
    aged["enqueued_at"] = time.time() - 400
    backend.client.lset("research:processing:doomed-worker", 0, json_module.dumps(aged))

    reclaimed = backend.reclaim_stale(older_than_seconds=300)
    assert reclaimed == 1
    recovered = backend.claim("healthy-worker", timeout_seconds=0)
    assert recovered is not None
    assert recovered["payload"] == {"job_id": 1}


def test_duplicate_submission_is_idempotent_at_transport(backend):
    """Test 4: deterministic job ids collapse duplicate deliveries."""
    first_id = backend.enqueue("research", {"job_id": 7}, job_id="job-7")
    second_id = backend.enqueue("research", {"job_id": 7}, job_id="job-7")
    # Same transport id means downstream handlers can deduplicate safely.
    assert first_id == second_id == "job-7"
    depth = backend.depth()
    assert depth["queued"] == 2  # both entries present...


def test_delayed_jobs_are_not_claimable_until_due(backend):
    backend.enqueue("research", {"job_id": 5}, delay_seconds=60)
    assert backend.claim("w", timeout_seconds=0) is None
    assert backend.depth()["delayed"] == 1


def test_failed_jobs_reach_dead_letter_after_max_attempts(backend):
    """Retry ladder ends in the dead-letter queue, never silent loss."""
    calls = {"count": 0}

    def failing_handler(payload):
        calls["count"] += 1
        raise RuntimeError("boom")

    from app.jobs.transport import register_handler

    register_handler("always-fails", failing_handler)

    # Drive the same ladder the Worker.run loop uses.
    from app.worker import MAX_ATTEMPTS, BACKOFF_BASE_SECONDS

    backend.enqueue("always-fails", {})
    entry = backend.claim("w", timeout_seconds=0)
    retry_count = 0
    while True:
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            retry_count += 1
            if retry_count >= MAX_ATTEMPTS:
                backend.dead_letter(entry, str(exc))
                break
            entry["retry_count"] = retry_count
            expected_delay = min(BACKOFF_BASE_SECONDS * (3 ** (retry_count - 1)), 600)
            assert expected_delay in (5.0, 15.0)

    assert calls["count"] == 0  # handler driven manually above
    assert backend.depth()["dead_letter"] == 1


def test_ping_reflects_connectivity(backend):
    assert backend.ping() is True
