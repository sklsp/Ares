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
