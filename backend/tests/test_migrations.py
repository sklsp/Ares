"""Migration determinism: fresh database and sequential upgrade paths.

Runs Alembic and the seeder in subprocesses so migrations are exercised in
isolation from the test session's own database/engine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run(args: list[str], url: str, timeout: int = 180) -> subprocess.CompletedProcess:
    environment = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        [sys.executable, *args],
        cwd=BACKEND_DIR, capture_output=True, text=True, timeout=timeout,
        env=environment,
    )


def test_fresh_database_reaches_head():
    """Empty DB -> all migrations -> expected tables and tenant columns."""
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{(Path(tmp) / 'fresh.db').as_posix()}"
        result = _run(["-m", "alembic", "upgrade", "head"], url)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

        from sqlalchemy import create_engine, inspect

        engine = create_engine(url)
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        expected = {
            "products", "inventory", "orders", "agent_runs", "agent_steps",
            "approval_requests", "external_stores", "external_products",
            "product_snapshots", "research_jobs", "opportunities",
            "opportunity_evidence", "organizations", "users", "sessions",
            "audit_logs",
        }
        missing = expected - tables
        assert not missing, f"migrations did not create: {missing}"
        for table in ("agent_runs", "external_stores", "research_jobs", "opportunities"):
            columns = {c["name"] for c in inspector.get_columns(table)}
            assert "organization_id" in columns, f"{table} missing organization_id"
        engine.dispose()


def test_seed_upgrades_existing_database_in_place():
    """app.seed --keep applies pending migrations to an existing database."""
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{(Path(tmp) / 'existing.db').as_posix()}"

        # Build a legacy (pre-tenant) schema by upgrading to 0004 only.
        result = _run(["-m", "alembic", "upgrade", "0004_identity"], url)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

        # Seed with --keep must upgrade to head without resetting data.
        result = _run(["-m", "app.seed", "--keep"], url)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

        from sqlalchemy import create_engine, inspect, text

        engine = create_engine(url)
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("research_jobs")}
        assert "organization_id" in columns

        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        engine.dispose()
        assert version == "0005_tenant_scoping"


def test_upgrade_at_head_is_idempotent():
    """Running upgrade twice at head is a no-op, not an error."""
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{(Path(tmp) / 'twice.db').as_posix()}"
        assert _run(["-m", "alembic", "upgrade", "head"], url).returncode == 0
        assert _run(["-m", "alembic", "upgrade", "head"], url).returncode == 0
