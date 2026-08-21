"""API key authentication behavior."""

from __future__ import annotations

import pytest


@pytest.fixture
def secured_client(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "api_key", "test-secret-key")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_requests_without_key_are_rejected_when_configured(secured_client):
    assert secured_client.get("/products").status_code == 401
    assert secured_client.get("/analytics/summary").status_code == 401


def test_requests_with_wrong_key_are_rejected(secured_client):
    response = secured_client.get("/products", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_requests_with_correct_key_pass(secured_client):
    response = secured_client.get("/products", headers={"X-API-Key": "test-secret-key"})
    assert response.status_code == 200


def test_api_is_open_by_default_for_local_development(client):
    assert client.get("/health").status_code == 200
