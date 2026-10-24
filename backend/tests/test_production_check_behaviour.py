"""Behavioural tests for app/production_check.py (fail-fast config validation).

validate_production() reads the process-wide settings singleton from
app.config, so each test monkeypatches exactly one setting on top of a fully
healthy production baseline and asserts the exact problem message(s) that come
back. main() is tested end to end through its real stdout/stderr output and
exit code (0 = ready, 1 = blocked).
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.production_check import ProductionConfigError, main, validate_production

# Exact problem messages as written in production_check.py.
SQLITE_MSG = (
    "DATABASE_URL uses SQLite. Distributed deployments require "
    "PostgreSQL: postgresql+psycopg://user:pass@host:5432/db"
)
REDIS_MSG = (
    "REDIS_URL is not set. Distributed job execution and shared rate "
    "limiting require Redis: redis://host:6379/0"
)
WORKER_MSG = (
    "EMBEDDED_WORKER=true in production mode. Set EMBEDDED_WORKER=false "
    "on API replicas and run dedicated `python -m app.worker` processes."
)
REGISTRATION_MSG = (
    "ALLOW_SELF_REGISTRATION=true lets anyone create a viewer account that can "
    "read the catalog. Set ALLOW_SELF_REGISTRATION=false and create accounts as "
    "an admin (the first one with `python -m app.create_user EMAIL --role admin`)."
)
API_KEY_MSG = "LLM_PROVIDER=openai_compatible but OPENAI_API_KEY is empty"
TEST_PROVIDER_MSG = (
    "LLM_PROVIDER=test is the deterministic CI provider. It is not a "
    "live model and must not serve production traffic"
)


def _clean_settings(monkeypatch) -> None:
    """Point every checked setting at a healthy production value."""
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+psycopg://ares:secret@db.internal:5432/ares",
    )
    monkeypatch.setattr(settings, "redis_url", "redis://cache.internal:6379/0")
    monkeypatch.setattr(settings, "embedded_worker", False)
    monkeypatch.setattr(settings, "allow_self_registration", False)
    monkeypatch.setattr(settings, "llm_provider", "ollama")


def test_sqlite_database_url_is_flagged(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "database_url", "sqlite:///./local.db")

    assert validate_production() == [SQLITE_MSG]


def test_missing_redis_url_is_flagged(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "redis_url", None)

    assert validate_production() == [REDIS_MSG]


def test_embedded_worker_in_production_mode_is_flagged(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "embedded_worker", True)

    assert validate_production() == [WORKER_MSG]


def test_self_registration_enabled_is_flagged(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "allow_self_registration", True)

    assert validate_production() == [REGISTRATION_MSG]


def test_openai_compatible_without_api_key_is_flagged(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "openai_api_key", None)

    assert validate_production() == [API_KEY_MSG]


def test_openai_compatible_with_api_key_passes(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "openai_api_key", "sk-live-123")

    assert validate_production() == []


def test_test_llm_provider_is_flagged(monkeypatch):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "llm_provider", "test")

    assert validate_production() == [TEST_PROVIDER_MSG]


def test_clean_production_config_reports_no_problems(monkeypatch):
    _clean_settings(monkeypatch)

    assert validate_production() == []


def test_main_blocked_returns_1_and_lists_every_problem_on_stderr(
    monkeypatch, capsys
):
    _clean_settings(monkeypatch)
    monkeypatch.setattr(settings, "database_url", "sqlite:///./local.db")
    monkeypatch.setattr(settings, "redis_url", None)

    assert main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    # The header string already ends in a newline and print adds one more,
    # so the blocked report has a blank line before the problem list.
    assert (
        captured.err
        == "Production configuration is BLOCKED:\n\n"
        f"  - {SQLITE_MSG}\n"
        f"  - {REDIS_MSG}\n"
    )


def test_main_ready_returns_0_and_prints_ok(monkeypatch, capsys):
    _clean_settings(monkeypatch)

    assert main() == 0

    captured = capsys.readouterr()
    assert captured.out == "Production configuration OK\n"
    assert captured.err == ""


def test_production_config_error_is_a_runtime_error():
    with pytest.raises(RuntimeError, match="missing redis"):
        raise ProductionConfigError("missing redis")
