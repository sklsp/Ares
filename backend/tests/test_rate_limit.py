"""Rate limiting behavior on expensive endpoints."""

from __future__ import annotations

import pytest

from app.services.rate_limit import reset_limits


@pytest.fixture(autouse=True)
def _clean_limits():
    reset_limits()
    yield
    reset_limits()


def test_agent_run_is_rate_limited(client, llm):
    from tests.fakes import answer

    llm.fallback = answer("Done.")
    statuses = [
        client.post("/agent/run", json={"message": f"run {i}"}).status_code
        for i in range(12)
    ]
    assert statuses.count(202) >= 1
    assert 429 in statuses


def test_research_job_is_rate_limited(client):
    statuses = [
        client.post("/intelligence/jobs", json={
            "objective": f"Investigation {i}", "query": f"niche {i}",
        }).status_code
        for i in range(12)
    ]
    assert statuses.count(202) >= 1
    assert 429 in statuses


def test_rate_limit_can_be_disabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "rate_limit_disabled", True)
    statuses = [
        client.post("/intelligence/jobs", json={
            "objective": f"Open investigation {i}", "query": f"open {i}",
        }).status_code
        for i in range(12)
    ]
    assert 429 not in statuses
