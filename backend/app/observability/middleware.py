"""Request instrumentation and correlation IDs."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import Timer, inc

CORRELATION_HEADER = "X-Correlation-ID"


class instrument_requests(BaseHTTPMiddleware):
    """Records request count/latency/status and propagates a correlation ID.

    The ID is accepted from clients (X-Correlation-ID) or generated, stored on
    request.state for logging, echoed in the response, and counted in metrics.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex[:16]
        request.state.correlation_id = correlation_id

        method = request.method
        path = request.scope.get("route").path if request.scope.get("route") else request.url.path
        with Timer("http_request_duration_seconds", method=method, path=path):
            response = await call_next(request)
        inc("http_requests_total", method=method, path=path,
            status=str(response.status_code))
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
