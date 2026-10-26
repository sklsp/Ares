"""Behaviour tests for app/jobs/transport.py.

The Redis backend is exercised against an in-memory FakeRedis that
implements exactly the commands transport.py uses (lpush, rpoplpush,
brpoplpush, lrem, llen, lrange, zadd, zrangebyscore, zrem, zcard, keys,
ping), so list/sorted-set semantics are real and no network access or
Redis server is needed. The `redis` package itself is faked in sys.modules
for the get_backend() wiring test (it is not installed in this venv).
"""

from __future__ import annotations

import fnmatch
import json
import sys
import time
import types

import pytest

from app.jobs import transport

# --------------------------------------------------------------------------- #
# fake redis client
# --------------------------------------------------------------------------- #


class FakeRedis:
    """Minimal in-memory Redis for the commands transport.py uses."""

    def __init__(self):
        self.lists: dict[str, list] = {}
        self.zsets: dict[str, dict] = {}
        self.ping_error = None
        self.calls: list[tuple] = []

    # -- lists ----------------------------------------------------------
    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def rpoplpush(self, source, destination):
        src = self.lists.get(source)
        if not src:
            return None
        value = src.pop()  # pop from the tail (rpop semantics)
        self.lpush(destination, value)
        return value

    def brpoplpush(self, source, destination, timeout=0):
        self.calls.append(("brpoplpush", source, destination, timeout))
        return self.rpoplpush(source, destination)

    def lrem(self, key, count, value):
        lst = self.lists.get(key)
        if not lst:
            return 0
        matches = [i for i, item in enumerate(lst) if item == value]
        if count > 0:
            matches = matches[:count]
        elif count < 0:
            matches = matches[-abs(count):]
        removed = 0
        for index in sorted(matches, reverse=True):
            del lst[index]
            removed += 1
        return removed

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lrange(self, key, start, stop):
        lst = self.lists.get(key, [])
        if stop == -1:
            return list(lst[start:])
        return list(lst[start : stop + 1])

    # -- sorted sets ------------------------------------------------------
    def zadd(self, key, mapping):
        zs = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in zs:
                added += 1
            zs[member] = score
        return added

    def zrangebyscore(self, key, low, high):
        lo = float(low)
        hi = float(high)
        return [m for m, s in self.zsets.get(key, {}).items() if lo <= s <= hi]

    def zrem(self, key, *members):
        zs = self.zsets.setdefault(key, {})
        removed = 0
        for member in members:
            if zs.pop(member, None) is not None:
                removed += 1
        return removed

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    # -- keys / ping ------------------------------------------------------
    def keys(self, pattern):
        all_keys = set(self.lists) | set(self.zsets)
        return [k for k in sorted(all_keys) if fnmatch.fnmatchcase(k, pattern)]

    def ping(self):
        if self.ping_error is not None:
            raise self.ping_error
        return True


# --------------------------------------------------------------------------- #
# fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def reset_transport_state():
    """Reset module-level backend and handler registry around each test."""
    saved_backend = transport._backend
    saved_handlers = dict(transport._handlers)
    transport._backend = None
    transport._handlers.clear()
    yield
    transport._backend = saved_backend
    transport._handlers.clear()
    transport._handlers.update(saved_handlers)


def make_redis_backend():
    client = FakeRedis()
    return transport.RedisQueueBackend(client), client


# --------------------------------------------------------------------------- #
# handler registry
# --------------------------------------------------------------------------- #


def test_register_and_get_handler():
    def handler(payload):
        pass

    assert transport.get_handler("research") is None
    transport.register_handler("research", handler)
    assert transport.get_handler("research") is handler


def test_register_handler_overwrites_previous():
    first = lambda payload: None  # noqa: E731
    second = lambda payload: None  # noqa: E731

    transport.register_handler("t", first)
    transport.register_handler("t", second)

    assert transport.get_handler("t") is second


# --------------------------------------------------------------------------- #
# RedisQueueBackend.enqueue / claim
# --------------------------------------------------------------------------- #


def test_redis_enqueue_immediate_pushes_json_entry():
    backend, client = make_redis_backend()

    job_id = backend.enqueue("research", {"sku": "RUN-001"}, job_id="job-42")

    assert job_id == "job-42"
    (raw,) = client.lists[transport.QUEUE_KEY]
    entry = json.loads(raw)
    assert entry["id"] == "job-42"
    assert entry["type"] == "research"
    assert entry["payload"] == {"sku": "RUN-001"}
    assert abs(entry["enqueued_at"] - time.time()) < 5


def test_redis_enqueue_without_job_id_generates_uuid_hex():
    backend, client = make_redis_backend()

    job_id = backend.enqueue("research", {})

    assert len(job_id) == 32
    int(job_id, 16)  # must be valid hex
    entry = json.loads(client.lists[transport.QUEUE_KEY][0])
    assert entry["id"] == job_id


def test_redis_enqueue_delayed_goes_to_sorted_set():
    backend, client = make_redis_backend()

    before = time.time()
    job_id = backend.enqueue("research", {}, delay_seconds=30)
    after = time.time()

    assert transport.QUEUE_KEY not in client.lists
    (member,) = list(client.zsets["research:delayed"])
    score = client.zsets["research:delayed"][member]
    assert before + 30 <= score <= after + 30
    entry = json.loads(member)
    assert entry["id"] == job_id


def test_redis_claim_nonblocking_moves_entry_to_processing():
    backend, client = make_redis_backend()
    backend.enqueue("research", {"n": 1}, job_id="a")
    backend.enqueue("research", {"n": 2}, job_id="b")

    entry = backend.claim("w1", timeout_seconds=0)

    # LPUSH + RPOPLPUSH: the oldest enqueued job is claimed first (FIFO).
    assert entry["id"] == "a"
    remaining = client.lists[transport.QUEUE_KEY]
    assert len(remaining) == 1 and json.loads(remaining[0])["id"] == "b"
    processing_key = f"{transport.PROCESSING_KEY}:w1"
    assert [json.loads(r)["id"] for r in client.lists[processing_key]] == ["a"]


def test_redis_claim_returns_raw_value_as_stored():
    backend, client = make_redis_backend()
    backend.enqueue("research", {"n": 1}, job_id="a")

    entry = backend.claim("w1", timeout_seconds=0)

    # _raw is the exact value Redis returned (what lpush stored), so a later
    # lrem of it matches byte-for-byte.
    processing_key = f"{transport.PROCESSING_KEY}:w1"
    (stored,) = client.lists[processing_key]
    assert entry["_raw"] == stored


def test_redis_claim_blocking_uses_brpoplpush_with_int_timeout():
    backend, client = make_redis_backend()
    backend.enqueue("research", {}, job_id="a")

    entry = backend.claim("w2", timeout_seconds=2.7)

    assert entry["id"] == "a"
    # Blocking claim truncates the float timeout to an int for BRPOPLPUSH.
    assert client.calls == [
        ("brpoplpush", transport.QUEUE_KEY, f"{transport.PROCESSING_KEY}:w2", 2)
    ]


def test_redis_claim_empty_queue_returns_none():
    backend, client = make_redis_backend()

    assert backend.claim("w1", timeout_seconds=0) is None
    assert backend.claim("w1", timeout_seconds=5) is None
    assert not client.lists


def test_redis_claim_malformed_entry_is_discarded():
    backend, client = make_redis_backend()
    client.lpush(transport.QUEUE_KEY, "this-is-not-json")

    entry = backend.claim("w1", timeout_seconds=0)

    assert entry is None
    processing_key = f"{transport.PROCESSING_KEY}:w1"
    assert client.lists.get(processing_key, []) == []  # lrem cleaned it up


def test_redis_claim_promotes_due_delayed_entries():
    backend, client = make_redis_backend()
    now = time.time()
    due_raw = json.dumps({"id": "due", "type": "research", "payload": {}, "enqueued_at": now})
    future_raw = json.dumps({"id": "future", "type": "research", "payload": {}, "enqueued_at": now})
    client.zadd("research:delayed", {due_raw: now - 1, future_raw: now + 999})

    entry = backend.claim("w1", timeout_seconds=0)

    assert entry["id"] == "due"
    # The not-yet-due entry stays in the sorted set.
    assert client.zsets["research:delayed"] == {future_raw: now + 999}


def test_redis_promote_delayed_noop_when_zset_empty():
    backend, client = make_redis_backend()

    # No delayed zset at all; claim must not raise and returns None.
    assert backend.claim("w1", timeout_seconds=0) is None
    assert "research:delayed" not in client.zsets


# --------------------------------------------------------------------------- #
# RedisQueueBackend.complete / requeue / dead_letter
# --------------------------------------------------------------------------- #


def _claimed_entry(backend, client, worker_id="w1"):
    """Enqueue one job and claim it; returns the entry exactly as stored."""
    backend.enqueue("research", {"n": 1}, job_id="job-1")
    return backend.claim(worker_id, timeout_seconds=0)


def test_processing_value_prefers_stored_raw():
    entry = {"id": "job-1", "_raw": '{"id": "job-1"}'}

    assert transport.RedisQueueBackend._processing_value(entry) == '{"id": "job-1"}'


def test_processing_value_without_raw_drops_worker_id():
    entry = {"id": "job-1", "type": "research", "worker_id": "w1"}

    assert (transport.RedisQueueBackend._processing_value(entry)
            == '{"id": "job-1", "type": "research"}')


def test_redis_complete_untagged_entry_targets_bare_processing_key():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)

    # Without a worker_id tag the key suffix is empty ("research:processing:"),
    # so nothing is removed from the claiming worker's list.
    backend.complete(entry)

    processing_key = f"{transport.PROCESSING_KEY}:w1"
    assert [json.loads(r)["id"] for r in client.lists[processing_key]] == ["job-1"]
    assert f"{transport.PROCESSING_KEY}:" not in client.lists


def test_redis_complete_without_worker_id_uses_empty_suffix_key():
    backend, client = make_redis_backend()
    # An entry that never got a worker_id tag targets the bare processing key.
    backend.complete({"id": "x"})

    assert f"{transport.PROCESSING_KEY}:" not in client.lists


def test_redis_complete_with_worker_tagged_entry_removes_stored_raw():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)
    entry["worker_id"] = "w1"  # exactly what app/worker.py does after claim

    backend.complete(entry)

    assert [json.loads(r)["id"] for r in client.lists[f"{transport.PROCESSING_KEY}:w1"]] == []


def test_redis_requeue_immediate_enqueues_copy_and_removes_original():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)

    backend.requeue(entry)

    (raw,) = client.lists[transport.QUEUE_KEY]
    requeued = json.loads(raw)
    assert requeued["id"] == "job-1"
    assert requeued["type"] == "research"
    assert requeued["payload"] == {"n": 1}
    # The original is removed from processing.
    processing_key = f"{transport.PROCESSING_KEY}:w1"
    assert client.lists[processing_key] == []


def test_redis_requeue_delayed_goes_to_sorted_set():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)

    backend.requeue(entry, delay_seconds=60)

    assert client.lists.get(transport.QUEUE_KEY, []) == []
    (member,) = list(client.zsets["research:delayed"])
    assert json.loads(member)["id"] == "job-1"


def test_redis_requeue_missing_fields_uses_defaults():
    backend, client = make_redis_backend()

    # No type/payload/id keys at all.
    backend.requeue({})

    (raw,) = client.lists[transport.QUEUE_KEY]
    requeued = json.loads(raw)
    assert requeued["type"] == "research"
    assert requeued["payload"] == {}


def test_redis_dead_letter_parks_truncated_error():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)
    error = "boom-" + "x" * 2500

    backend.dead_letter(entry, error)

    (raw,) = client.lists[transport.DEAD_LETTER_KEY]
    parked = json.loads(raw)
    assert parked["id"] == "job-1"
    assert len(parked["error"]) == 2000
    assert parked["error"].startswith("boom-")
    assert abs(parked["failed_at"] - time.time()) < 5
    # The original is removed from processing.
    processing_key = f"{transport.PROCESSING_KEY}:w1"
    assert client.lists[processing_key] == []


def test_redis_dead_letter_short_error_kept_verbatim():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)

    backend.dead_letter(entry, "short failure")

    parked = json.loads(client.lists[transport.DEAD_LETTER_KEY][0])
    assert parked["error"] == "short failure"


def test_redis_dead_letter_with_worker_tagged_entry_removes_stored_raw():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)
    entry["worker_id"] = "w1"  # exactly what app/worker.py does after claim

    backend.dead_letter(entry, "boom")

    assert [json.loads(r)["id"] for r in client.lists[f"{transport.PROCESSING_KEY}:w1"]] == []
    dead = client.lists[transport.DEAD_LETTER_KEY]
    assert len(dead) == 1 and json.loads(dead[0])["id"] == "job-1"
    parked = json.loads(dead[0])
    assert "_raw" not in parked and parked["error"] == "boom"


def test_redis_requeue_with_worker_tagged_entry_removes_stored_raw():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)
    entry["worker_id"] = "w1"  # exactly what app/worker.py does after claim

    backend.requeue(entry)

    assert [json.loads(r)["id"] for r in client.lists[f"{transport.PROCESSING_KEY}:w1"]] == []


@pytest.mark.xfail(
    strict=True,
    reason="bug: requeue() enqueues only id, type and payload, so retry_count is lost "
    "and a failing job never dead-letters",
)
def test_redis_requeue_keeps_retry_count():
    backend, client = make_redis_backend()
    entry = _claimed_entry(backend, client)
    entry["worker_id"] = "w1"  # exactly what app/worker.py does after claim
    entry["retry_count"] = 2

    backend.requeue(entry)

    (raw,) = client.lists[transport.QUEUE_KEY]
    assert json.loads(raw)["retry_count"] == 2


# --------------------------------------------------------------------------- #
# RedisQueueBackend.reclaim_stale / depth / ping
# --------------------------------------------------------------------------- #


def _parked_raw(job_id, enqueued_at):
    return json.dumps({"id": job_id, "type": "research", "payload": {},
                       "enqueued_at": enqueued_at})


def test_redis_reclaim_stale_moves_only_old_entries_back():
    backend, client = make_redis_backend()
    now = time.time()
    stale_key = f"{transport.PROCESSING_KEY}:w1"
    fresh_key = f"{transport.PROCESSING_KEY}:w2"
    client.lpush(stale_key, _parked_raw("old", now - 3600))
    client.lpush(fresh_key, _parked_raw("new", now - 5))

    reclaimed = backend.reclaim_stale(older_than_seconds=60)

    assert reclaimed == 1
    (raw,) = client.lists[transport.QUEUE_KEY]
    assert json.loads(raw)["id"] == "old"
    # The fresh entry stays parked in its worker's processing list.
    assert [json.loads(r)["id"] for r in client.lists[fresh_key]] == ["new"]
    assert client.lists.get(stale_key, []) == []


def test_redis_reclaim_stale_skips_malformed_and_counts_missing_timestamp_as_stale():
    backend, client = make_redis_backend()
    key = f"{transport.PROCESSING_KEY}:w3"
    client.lpush(key, "not-json")
    # Missing enqueued_at defaults to 0, so the entry is always stale.
    no_ts_raw = json.dumps({"id": "no-ts", "type": "research", "payload": {}})
    client.lpush(key, no_ts_raw)

    reclaimed = backend.reclaim_stale(older_than_seconds=60)

    assert reclaimed == 1
    (raw,) = client.lists[transport.QUEUE_KEY]
    assert json.loads(raw)["id"] == "no-ts"
    # The malformed entry is left untouched in the processing list.
    assert client.lists[key] == ["not-json"]


def test_redis_reclaim_stale_no_processing_keys_returns_zero():
    backend, client = make_redis_backend()

    assert backend.reclaim_stale(older_than_seconds=60) == 0
    assert transport.QUEUE_KEY not in client.lists


def test_redis_depth_counts_all_buckets():
    backend, client = make_redis_backend()
    now = time.time()
    client.lpush(transport.QUEUE_KEY, _parked_raw("q1", now))
    client.lpush(transport.QUEUE_KEY, _parked_raw("q2", now))
    client.zadd("research:delayed", {_parked_raw("d1", now): now + 60})
    client.lpush(transport.DEAD_LETTER_KEY, _parked_raw("dead", now))
    client.lpush(f"{transport.PROCESSING_KEY}:w1", _parked_raw("p1", now))
    client.lpush(f"{transport.PROCESSING_KEY}:w2", _parked_raw("p2", now))

    assert backend.depth() == {
        "queued": 2,
        "delayed": 1,
        "dead_letter": 1,
        "processing": 2,
    }


def test_redis_depth_empty_client_is_all_zeros():
    backend, client = make_redis_backend()

    assert backend.depth() == {
        "queued": 0,
        "delayed": 0,
        "dead_letter": 0,
        "processing": 0,
    }


def test_redis_ping_true_when_client_healthy():
    backend, client = make_redis_backend()

    assert backend.ping() is True


def test_redis_ping_false_when_client_raises():
    backend, client = make_redis_backend()
    client.ping_error = ConnectionError("connection refused")

    assert backend.ping() is False


# --------------------------------------------------------------------------- #
# InlineQueueBackend
# --------------------------------------------------------------------------- #


def test_inline_enqueue_runs_registered_handler_and_records_entry():
    inline = transport.InlineQueueBackend()
    calls: list[dict] = []
    transport.register_handler("research", lambda payload: calls.append(payload))

    job_id = inline.enqueue("research", {"sku": "RUN-002"}, job_id="inline-1")

    assert job_id == "inline-1"
    assert calls == [{"sku": "RUN-002"}]
    (entry,) = inline.executed
    assert entry["id"] == "inline-1"
    assert entry["type"] == "research"
    assert entry["payload"] == {"sku": "RUN-002"}
    assert entry["worker_id"] == "inline"


def test_inline_enqueue_without_handler_still_records_entry():
    inline = transport.InlineQueueBackend()

    job_id = inline.enqueue("unknown-type", {})

    int(job_id, 16)  # generated uuid hex id
    (entry,) = inline.executed
    assert entry["type"] == "unknown-type"


def test_inline_enqueue_ignores_delay_and_runs_immediately():
    inline = transport.InlineQueueBackend()
    calls: list[dict] = []
    transport.register_handler("research", lambda payload: calls.append(payload))

    job_id = inline.enqueue("research", {"sku": "RUN-003"}, delay_seconds=5,
                            job_id="inline-d")

    assert job_id == "inline-d"
    # The inline backend ignores delay_seconds and executes synchronously.
    assert calls == [{"sku": "RUN-003"}]
    (entry,) = inline.executed
    assert entry["worker_id"] == "inline"


def test_inline_backend_noop_lifecycle_methods():
    inline = transport.InlineQueueBackend()
    entry = {"id": "x"}

    assert inline.claim("w1") is None
    inline.complete(entry)
    inline.requeue(entry, delay_seconds=0)
    inline.dead_letter(entry, "err")
    assert inline.reclaim_stale(60) == 0
    assert inline.depth() == {
        "queued": 0,
        "delayed": 0,
        "dead_letter": 0,
        "processing": 0,
    }
    assert inline.ping() is True


# --------------------------------------------------------------------------- #
# get_backend / set_backend wiring
# --------------------------------------------------------------------------- #


def test_get_backend_without_redis_url_returns_inline_singleton():
    from app.config import settings

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(transport, "_backend", None)
        monkeypatch.setattr(settings, "redis_url", None)
        first = transport.get_backend()
        second = transport.get_backend()
    finally:
        monkeypatch.undo()

    assert isinstance(first, transport.InlineQueueBackend)
    assert first is second


def test_get_backend_with_redis_url_builds_redis_backend():
    from app.config import settings

    fake_client = FakeRedis()
    captured: dict[str, object] = {}

    class FakeRedisClass:
        @classmethod
        def from_url(cls, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return fake_client

    fake_module = types.ModuleType("redis")
    fake_module.Redis = FakeRedisClass  # type: ignore[attr-defined]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(transport, "_backend", None)
        monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
        monkeypatch.setitem(sys.modules, "redis", fake_module)
        backend = transport.get_backend()
    finally:
        monkeypatch.undo()

    assert isinstance(backend, transport.RedisQueueBackend)
    assert backend.client is fake_client
    assert captured["url"] == "redis://localhost:6379/0"
    assert captured["decode_responses"] is True


def test_set_backend_override_and_reset():
    from app.config import settings

    override = transport.InlineQueueBackend()
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(transport, "_backend", None)
        monkeypatch.setattr(settings, "redis_url", None)
        assert transport.get_backend() is not override  # freshly cached backend
        transport.set_backend(override)
        assert transport.get_backend() is override

        transport.set_backend(None)
        assert transport._backend is None
    finally:
        monkeypatch.undo()

