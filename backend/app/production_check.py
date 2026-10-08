"""Production configuration validation.

Fails fast with actionable messages when a production deployment is
misconfigured, instead of starting in a misleading half-working state.

Usage:
    python -m app.production_check     # exit 0 = ready, 1 = blocked

Triggered automatically by the worker and API when EMBEDDED_WORKER=false
(production mode), because that implies a distributed deployment where
missing Redis or PostgreSQL cannot fall back to local defaults.
"""

from __future__ import annotations

import sys


class ProductionConfigError(RuntimeError):
    pass


def validate_production() -> list[str]:
    """Return a list of blocking configuration problems (empty = ready)."""
    from app.config import settings

    problems: list[str] = []

    if settings.database_url.startswith("sqlite"):
        problems.append(
            "DATABASE_URL uses SQLite. Distributed deployments require "
            "PostgreSQL: postgresql+psycopg://user:pass@host:5432/db"
        )

    if not settings.redis_url:
        problems.append(
            "REDIS_URL is not set. Distributed job execution and shared rate "
            "limiting require Redis: redis://host:6379/0"
        )

    if settings.embedded_worker:
        problems.append(
            "EMBEDDED_WORKER=true in production mode. Set EMBEDDED_WORKER=false "
            "on API replicas and run dedicated `python -m app.worker` processes."
        )

    if settings.allow_self_registration:
        problems.append(
            "ALLOW_SELF_REGISTRATION=true lets anyone create a viewer account that can "
            "read the catalog. Set ALLOW_SELF_REGISTRATION=false and create accounts as "
            "an admin (the first one with `python -m app.create_user EMAIL --role admin`)."
        )

    if settings.llm_provider == "openai_compatible" and not settings.openai_api_key:
        problems.append("LLM_PROVIDER=openai_compatible but OPENAI_API_KEY is empty")

    if settings.llm_provider == "test":
        problems.append(
            "LLM_PROVIDER=test is the deterministic CI provider — it is not a "
            "live model and must not serve production traffic"
        )

    return problems


def main() -> int:
    problems = validate_production()
    if problems:
        print("Production configuration is BLOCKED:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Production configuration OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
