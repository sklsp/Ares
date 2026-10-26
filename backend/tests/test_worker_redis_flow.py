"""End-to-end worker flow against FakeRedis (no network, no real Redis).

A job whose handler always fails must be retried with backoff and then
dead-lettered after MAX_ATTEMPTS attempts, leaving nothing in the queue,
the delayed set or any processing list. Uses the real Worker from
app/worker.py and the FakeRedis from tests/test_transport_behaviour.py;
only transport's clock is faked so requeue delays come due instantly (the
worker keeps the real time module).
"""

from __future__ import annotations

import json

from app.jobs import transport
from app.worker import MAX_ATTEMPTS, Worker
from tests.test_transport_behaviour import FakeRedis


class _FakeClock:
    """Stands in for the ``time`` module inside app.jobs.transport only."""

    def __init__(self) -> None:
        self._now = 1_000_000.0

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_failing_job_reaches_dead_letter_after_max_attempts(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(transport, "time", clock)

    saved_handlers = dict(transport._handlers)
    transport._handlers.clear()

    def boom(payload):
        raise RuntimeError("boom")

    transport.register_handler("research", boom)
    try:
        client = FakeRedis()
        backend = transport.RedisQueueBackend(client)
        worker = Worker()

        backend.enqueue("research", {"url": "https://example.com"}, job_id="job-1")

        processed = 0
        while True:
            clock.advance(10_000)
            if not worker._work_once(backend):
                break
            processed += 1
    finally:
        transport._handlers.clear()
        transport._handlers.update(saved_handlers)

    assert processed == MAX_ATTEMPTS
    assert worker.failed == MAX_ATTEMPTS

    dead = client.lists[transport.DEAD_LETTER_KEY]
    assert len(dead) == 1
    entry = json.loads(dead[0])
    assert entry["error"] == "boom"
    assert entry["retry_count"] == MAX_ATTEMPTS - 1

    assert not client.lists.get(transport.QUEUE_KEY, [])
    assert not client.zsets.get("research:delayed", {})
    for key in client.lists:
        if key.startswith(f"{transport.PROCESSING_KEY}:"):
            assert not client.lists[key]


def test_successful_job_leaves_nothing_behind(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(transport, "time", clock)

    saved_handlers = dict(transport._handlers)
    transport._handlers.clear()

    seen = []

    def ok(payload):
        seen.append(payload)

    transport.register_handler("research", ok)
    try:
        client = FakeRedis()
        backend = transport.RedisQueueBackend(client)
        worker = Worker()

        backend.enqueue("research", {"url": "https://example.com"}, job_id="job-2")

        clock.advance(10_000)
        assert worker._work_once(backend) is True
    finally:
        transport._handlers.clear()
        transport._handlers.update(saved_handlers)

    assert seen == [{"url": "https://example.com"}]
    assert worker.processed == 1

    assert not client.lists.get(transport.QUEUE_KEY, [])
    assert not client.lists.get(transport.DEAD_LETTER_KEY, [])
    for key in client.lists:
        if key.startswith(f"{transport.PROCESSING_KEY}:"):
            assert not client.lists[key]


def test_reclaim_never_runs_a_dead_lettered_job_again(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(transport, "time", clock)

    saved_handlers = dict(transport._handlers)
    transport._handlers.clear()

    def boom(payload):
        raise RuntimeError("boom")

    transport.register_handler("research", boom)
    try:
        client = FakeRedis()
        backend = transport.RedisQueueBackend(client)
        worker = Worker()

        # Item 14's flow: the failing job is retried, then dead-lettered.
        backend.enqueue("research", {"url": "https://example.com"}, job_id="job-3")
        while True:
            clock.advance(10_000)
            if not worker._work_once(backend):
                break

        # Nothing is parked in a processing list, so there is nothing to
        # reclaim and the dead-lettered job must not come back.
        assert backend.reclaim_stale(0) == 0
        assert not client.lists.get(transport.QUEUE_KEY, [])

        # The stale case still works: claim a fresh job with a worker that
        # then dies (never completes it), let the fake time run far ahead,
        # and reclaim must put the job back in the queue exactly once.
        backend.enqueue("research", {"url": "https://example.com"}, job_id="job-4")
        entry = backend.claim(worker.worker_id, timeout_seconds=0)
        assert entry is not None

        clock.advance(120)
        assert backend.reclaim_stale(60) == 1

        queue = client.lists.get(transport.QUEUE_KEY, [])
        assert len(queue) == 1
        assert json.loads(queue[0])["id"] == "job-4"
    finally:
        transport._handlers.clear()
        transport._handlers.update(saved_handlers)


def test_inline_backend_runs_jobs_on_enqueue():
    saved_handlers = dict(transport._handlers)
    transport._handlers.clear()

    seen = []

    def ok(payload):
        seen.append(payload)

    transport.register_handler("research", ok)
    try:
        backend = transport.InlineQueueBackend()

        job_id = backend.enqueue("research", {"url": "https://example.com"},
                                 job_id="job-5", retry_count=2)

        # The inline backend runs the handler immediately on enqueue.
        assert seen == [{"url": "https://example.com"}]
        assert len(backend.executed) == 1
        entry = backend.executed[0]
        assert entry["id"] == job_id == "job-5"
        assert entry["type"] == "research"
        assert entry["payload"] == {"url": "https://example.com"}
        assert entry["worker_id"] == "inline"
        assert entry["retry_count"] == 2

        # Nothing is queued or pending: the inline backend has no queue.
        assert backend.depth() == {"queued": 0, "delayed": 0, "dead_letter": 0,
                                   "processing": 0}
    finally:
        transport._handlers.clear()
        transport._handlers.update(saved_handlers)
