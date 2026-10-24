"""Behavioural tests for app/api/health.py (live / ready / health / tools / mcp).

Database failures are simulated by overriding the get_db dependency with a
session whose execute() raises; LLM unavailability is simulated by overriding
get_llm with a provider whose status() reports it down. The MCP endpoint's
failure path is driven by poisoning sys.modules so `import mcp_server` fails.
"""

from __future__ import annotations

import sys

from app.api.deps import get_llm
from app.db.base import get_db
from app.llm.base import ProviderStatus
from tests.fakes import FakeLLM


class BrokenDb:
    """A session whose every query blows up, like a dead database."""

    def execute(self, *args, **kwargs):
        raise RuntimeError("db down")


def _broken_db_override():
    yield BrokenDb()


class DownLLM(FakeLLM):
    """FakeLLM that reports itself unreachable with an explicit reason."""

    def status(self) -> ProviderStatus:
        return ProviderStatus(False, "fake", "scripted", detail="connection refused")


def test_liveness_reports_alive_with_version(client):
    response = client.get("/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive", "version": "1.0.0"}


def test_readiness_is_ready_when_database_answers(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": True, "version": "1.0.0"}


def test_readiness_returns_503_when_database_is_down(client):
    app = client.app
    app.dependency_overrides[get_db] = _broken_db_override
    try:
        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json() == {"status": "unready", "database": False}
    finally:
        app.dependency_overrides.clear()


def test_health_is_ok_when_database_and_llm_are_up(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert body["database"] == "ok"
    assert body["llm"] == {
        "provider": "fake",
        "model": "scripted",
        "available": True,
        "reason": None,
    }


def test_health_is_degraded_when_database_fails(client):
    app = client.app
    app.dependency_overrides[get_db] = _broken_db_override
    try:
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "error: db down"
        assert body["llm"]["available"] is True
    finally:
        app.dependency_overrides.clear()


def test_health_is_degraded_when_llm_reports_unavailable(client):
    app = client.app
    app.dependency_overrides[get_llm] = lambda: DownLLM()
    try:
        response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "ok"
        assert body["llm"] == {
            "provider": "fake",
            "model": "scripted",
            "available": False,
            "reason": "connection refused",
        }
    finally:
        app.dependency_overrides.clear()


def test_tools_lists_every_registry_tool_with_its_schemas(client):
    response = client.get("/tools")

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()}
    # The catalog and inventory reads the agent is built around.
    for name in ("get_products", "search_products", "get_inventory", "update_product"):
        assert name in tools
    for tool in tools.values():
        assert set(tool) == {
            "name",
            "description",
            "category",
            "access",
            "requires_approval",
            "input_schema",
            "output_schema",
        }
        assert tool["access"] in ("read", "write")


def test_mcp_health_reports_the_real_server(client):
    response = client.get("/mcp/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["server_name"] == "ecommerce-ops"
    assert body["transport"] == "stdio"
    assert body["tool_count"] == 9
    assert body["tools"] == [
        "get_products",
        "get_product",
        "search_products",
        "get_inventory",
        "get_low_stock_products",
        "get_best_sellers",
        "get_sales_summary",
        "analyze_product_content",
        "update_product",
    ]
    assert body["detail"] == "Start with: python -m mcp_server (LLM ollama)"


def test_mcp_health_reports_unavailable_when_import_fails(client, monkeypatch):
    # A sys.modules entry of None makes `import mcp_server` raise ImportError.
    monkeypatch.setitem(sys.modules, "mcp_server", None)

    response = client.get("/mcp/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["server_name"] == "ecommerce-ops"
    assert body["transport"] == "stdio"
    assert body["tool_count"] == 0
    assert body["tools"] == []
    assert "mcp_server" in (body["detail"] or "")

