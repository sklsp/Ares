"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.agent import runner
from app.api import admin, agent, analytics, approvals, health, intelligence, products
from app.api import auth as auth_api
from app.config import settings
from app.integrations.base import ProductNotFoundError, ProviderError
from app.logging_config import configure_logging, get_logger
from app.tools import ToolError, get_registry

configure_logging(settings.log_level)
logger = get_logger(__name__)

DESCRIPTION = """
Internal operations agent for an e-commerce catalog.

The agent chooses its own tools, reads from the store, drafts changes, and
**pauses for human approval before any write**. Every run is persisted with
its full step history.

* `POST /agent/run` starts a run, `GET /agent/runs/{id}/events` streams it.
* `GET /approvals` lists what is waiting for a human.
* `GET /tools` documents every tool the agent can reach.
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "API starting: %d tools, llm=%s, db=%s",
        len(get_registry()),
        settings.llm_provider,
        settings.database_url.split("@")[-1],
    )
    from app.db.base import SessionLocal
    from app.services.intelligence_jobs import recover_stale_jobs

    db = SessionLocal()
    try:
        recovered = recover_stale_jobs(db)
        if recovered:
            logger.info("Resubmitted %d interrupted research job(s)", recovered)
    finally:
        db.close()
    yield
    runner.shutdown()
    logger.info("API stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI E-commerce Operations Agent",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Metrics + correlation IDs on every request.
    from app.observability import instrument_requests

    app.add_middleware(instrument_requests)

    # The API answers JSON only: never sniff it into something else, never frame it, and
    # never leak a URL in the Referer header.
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> "Response":
        from app.observability.metrics import render

        return Response(content=render(), media_type="text/plain; version=0.0.4")

    # Global auth gate: enforced only when API_KEY is configured.
    from app.api.deps import require_api_key

    app.router.dependencies.append(Depends(require_api_key))

    for router in (
        health.router,
        auth_api.router,
        admin.router,
        agent.router,
        approvals.router,
        products.router,
        analytics.router,
        intelligence.router,
    ):
        app.include_router(router)

    _register_error_handlers(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProductNotFoundError)
    async def not_found(_: Request, exc: ProductNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": str(exc)})

    @app.exception_handler(ProviderError)
    async def provider_error(_: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=400, content={"error": "provider_error", "detail": str(exc)}
        )

    @app.exception_handler(ToolError)
    async def tool_error(_: Request, exc: ToolError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "tool_error", "detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "detail": exc.errors()},
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("Database error")
        return JSONResponse(
            status_code=503,
            content={"error": "database_error", "detail": "The database is unavailable"},
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "detail": str(exc)},
        )


app = create_app()
