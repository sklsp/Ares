"""Behavioural tests for app/services/rate_limit.py (local + Redis limiting).

The local limiter is a sliding window over time.monotonic() timestamps, so a
fake monotonic clock drives the expiry cases without sleeping. The Redis path
is exercised with in-memory fakes: a fake client object for _hit_distributed
and a fake `redis` module injected into sys.modules for _redis_client. The
process-global bucket map is reset around every test by conftest's autouse
_clean_rate_limits fixture, so each test starts from an empty limiter.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.services import rate_limit as rl

LIMITED_DETAIL = "Rate limit exceeded; slow down and retry shortly"


class FakeRedisClient:
    """Just enough of redis.Redis for the fixed-window counter."""

    def __init__(self, fail_incr=False):
        self.counts: dict[str, int] = {}
        self.expired: list[tuple[str, int]] = []
        self.fail_incr = fail_incr

    def incr(self, key: str) -> int:
        if self.fail_incr:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, ttl: int) -> None:
        self.expired.append((key, ttl))


def _make_request(host="203.0.113.7", forwarded=None, path="/agent/run"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
    }
    if host is not None:
        scope["client"] = (host, 1234)
    if forwarded is not None:
        scope["headers"] = [(b"x-forwarded-for", forwarded.encode())]
    return Request(scope)


# --- local sliding-window limiter ------------------------------------------


def test_local_limiter_blocks_after_the_limit():
    for _ in range(3):
        rl.check_rate_limit("user-a:/x", 3, window_seconds=60.0)

    with pytest.raises(HTTPException) as excinfo:
        rl.check_rate_limit("user-a:/x", 3, window_seconds=60.0)
    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == LIMITED_DETAIL


def test_local_limiter_window_expires_old_hits(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["now"])

    for _ in range(2):
        rl.check_rate_limit("user-b:/x", 2, window_seconds=60.0)
    with pytest.raises(HTTPException):
        rl.check_rate_limit("user-b:/x", 2, window_seconds=60.0)

    # One second past the window: both hits have expired again.
    clock["now"] += 61.0
    rl.check_rate_limit("user-b:/x", 2, window_seconds=60.0)


def test_reset_limits_clears_buckets():
    for _ in range(2):
        rl.check_rate_limit("user-c:/x", 2, window_seconds=60.0)
    with pytest.raises(HTTPException):
        rl.check_rate_limit("user-c:/x", 2, window_seconds=60.0)

    rl.reset_limits()
    rl.check_rate_limit("user-c:/x", 2, window_seconds=60.0)


# --- distributed (Redis) limiter -------------------------------------------


def test_distributed_counter_blocks_after_the_limit(monkeypatch):
    monkeypatch.setattr(rl.time, "time", lambda: 1_800_000.0)  # window == 30000
    client = FakeRedisClient()

    rl._hit_distributed(client, "user-d:/x", 2, 60.0)
    rl._hit_distributed(client, "user-d:/x", 2, 60.0)
    with pytest.raises(HTTPException) as excinfo:
        rl._hit_distributed(client, "user-d:/x", 2, 60.0)

    assert excinfo.value.status_code == 429
    assert excinfo.value.detail == LIMITED_DETAIL
    # First hit of the window sets the expiry once, one second past the window.
    assert client.expired == [("ratelimit:user-d:/x:30000", 61)]


def test_distributed_redis_failure_falls_back_to_local():
    client = FakeRedisClient(fail_incr=True)

    # incr always fails, so every call degrades to the local limiter.
    rl._hit_distributed(client, "user-e:/x", 2, 60.0)
    rl._hit_distributed(client, "user-e:/x", 2, 60.0)
    with pytest.raises(HTTPException):
        rl._hit_distributed(client, "user-e:/x", 2, 60.0)


def test_check_rate_limit_uses_distributed_when_redis_available(monkeypatch):
    monkeypatch.setattr(rl.time, "time", lambda: 1_800_000.0)
    client = FakeRedisClient()
    monkeypatch.setattr(rl, "_redis_client", lambda: client)

    rl.check_rate_limit("user-f:/x", 5, window_seconds=60.0)

    assert client.counts == {"ratelimit:user-f:/x:30000": 1}
    # The local bucket must stay untouched on the distributed path.
    assert "user-f:/x" not in rl._local_hits


# --- _redis_client ----------------------------------------------------------


def test_redis_client_returns_none_without_url(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", None)

    assert rl._redis_client() is None


def test_redis_client_returns_none_when_package_missing(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://cache:6379/0")
    # A sys.modules entry of None makes `import redis` raise ImportError.
    monkeypatch.setitem(sys.modules, "redis", None)

    assert rl._redis_client() is None


def test_redis_client_returns_none_when_ping_fails(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://cache:6379/0")

    class UnreachableRedis:
        @staticmethod
        def from_url(url, **kwargs):
            return UnreachableRedis()

        def ping(self):
            raise ConnectionRefusedError("no redis here")

    fake_module = types.ModuleType("redis")
    fake_module.Redis = UnreachableRedis
    monkeypatch.setitem(sys.modules, "redis", fake_module)

    assert rl._redis_client() is None


def test_redis_client_returns_pinged_client(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://cache:6379/0")
    seen = {}

    class WorkingRedis:
        @staticmethod
        def from_url(url, **kwargs):
            instance = WorkingRedis()
            seen["url"] = url
            seen["kwargs"] = kwargs
            return instance

        def ping(self):
            return True

    fake_module = types.ModuleType("redis")
    fake_module.Redis = WorkingRedis
    monkeypatch.setitem(sys.modules, "redis", fake_module)

    client = rl._redis_client()

    assert isinstance(client, WorkingRedis)
    assert seen["url"] == "redis://cache:6379/0"
    assert seen["kwargs"] == {"socket_connect_timeout": 1, "socket_timeout": 1}


# --- client identity ---------------------------------------------------------


def test_client_key_prefers_first_forwarded_ip():
    request = _make_request(host="10.0.0.1", forwarded="203.0.113.7, 10.0.0.1")

    assert rl.client_key(request) == "203.0.113.7"


def test_client_key_falls_back_to_peer_host():
    request = _make_request(host="198.51.100.9")

    assert rl.client_key(request) == "198.51.100.9"


def test_client_key_is_unknown_without_a_peer():
    request = _make_request(host=None)

    assert rl.client_key(request) == "unknown"


# --- rate_limit dependency factory -------------------------------------------


def test_dependency_returns_immediately_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_disabled", True)
    # Bucket already full: a non-disabled dependency would raise 429.
    for _ in range(2):
        rl.check_rate_limit("10.0.0.1:/agent/run", 2, window_seconds=60.0)

    dependency = rl.rate_limit(limit=2, window_seconds=60.0)
    dependency(_make_request(host="10.0.0.1"))


def test_dependency_limits_by_client_host_and_url_path():
    # No route object in the scope: the URL path is used as the scope name.
    for _ in range(2):
        rl.check_rate_limit("198.51.100.9:/agent/run", 2, window_seconds=60.0)

    dependency = rl.rate_limit(limit=2, window_seconds=60.0)
    with pytest.raises(HTTPException) as excinfo:
        dependency(_make_request(host="198.51.100.9"))
    assert excinfo.value.status_code == 429


def test_dependency_uses_route_object_path_when_present():
    request = _make_request(host="198.51.100.9", path="/agent/run")
    request.scope["route"] = types.SimpleNamespace(path="/routed-name")

    for _ in range(2):
        rl.check_rate_limit("198.51.100.9:/routed-name", 2, window_seconds=60.0)

    dependency = rl.rate_limit(limit=2, window_seconds=60.0)
    with pytest.raises(HTTPException):
        dependency(request)


def test_dependency_honours_custom_key_by():
    for _ in range(2):
        rl.check_rate_limit("fixed-id:/agent/run", 2, window_seconds=60.0)

    dependency = rl.rate_limit(limit=2, window_seconds=60.0, key_by=lambda r: "fixed-id")
    with pytest.raises(HTTPException):
        dependency(_make_request(host="198.51.100.9"))
