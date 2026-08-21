"""In-process sliding-window rate limiting.

Sufficient for single-process deployments and as a per-replica limiter in
multi-process deployments (a shared Redis backend can replace the store
behind the same interface). Limits are applied to expensive endpoints.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException, Request, status

from app.config import settings

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)


def _hit(key: str, limit: int, window_seconds: float) -> None:
    now = time.monotonic()
    with _lock:
        bucket = _hits[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded; slow down and retry shortly",
            )
        bucket.append(now)


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return (forwarded.split(",")[0].strip() if forwarded else None) \
        or (request.client.host if request.client else "unknown")


def rate_limit(*, limit: int, window_seconds: float = 60.0,
               key_by: Callable[[Request], str] | None = None) -> Callable:
    """Dependency factory: raises 429 when the caller exceeds `limit`/window."""

    def dependency(request: Request) -> None:
        if settings.rate_limit_disabled:
            return
        identity = key_by(request) if key_by else client_key(request)
        route = request.scope.get("route")
        scope_name = getattr(route, "path", request.url.path)
        _hit(f"{identity}:{scope_name}", limit, window_seconds)

    return dependency


def reset_limits() -> None:
    with _lock:
        _hits.clear()
