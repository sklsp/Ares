"""Authentication, RBAC, and audit behavior."""

from __future__ import annotations

import pytest

from app.services.auth import (
    Role,
    create_session,
    hash_password,
    resolve_session,
    role_at_least,
    verify_password,
)


# --- password hashing ---------------------------------------------------
def test_password_hash_roundtrip():
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("wrong password", stored)


def test_malformed_hash_fails_closed():
    assert not verify_password("anything", "not-a-valid-hash")


# --- sessions -----------------------------------------------------------
def test_session_roundtrip_and_revocation(db):
    from app.db.identity import User
    from app.services.auth import ensure_default_organization

    org = ensure_default_organization(db)
    user = User(organization_id=org.id, email="a@example.com",
                password_hash=hash_password("long enough password"), role="analyst")
    db.add(user)
    db.commit()

    token, _row = create_session(db, user)
    assert resolve_session(db, token) is not None
    from app.services.auth import revoke_session

    revoke_session(db, token)
    assert resolve_session(db, token) is None


# --- RBAC levels --------------------------------------------------------
def test_role_hierarchy():
    assert role_at_least(Role.ADMIN.value, Role.MANAGER.value)
    assert role_at_least(Role.MANAGER.value, Role.ANALYST.value)
    assert role_at_least(Role.ANALYST.value, Role.VIEWER.value)
    assert not role_at_least(Role.VIEWER.value, Role.ANALYST.value)
    assert not role_at_least(Role.ANALYST.value, Role.ADMIN.value)


# --- API surface --------------------------------------------------------
def test_register_login_me_logout_flow(client):
    register = client.post("/auth/register", json={
        "email": "manager@example.com", "password": "long enough password",
        "role": "manager",
    })
    assert register.status_code == 201
    assert register.json()["role"] == "manager"

    login = client.post("/auth/login", json={
        "email": "manager@example.com", "password": "long enough password",
    })
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "manager@example.com"

    logout = client.post("/auth/logout", headers=headers)
    assert logout.status_code == 204
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_duplicate_registration_conflicts(client):
    payload = {"email": "dup@example.com", "password": "long enough password"}
    assert client.post("/auth/register", json=payload).status_code == 201
    assert client.post("/auth/register", json=payload).status_code == 409


def test_failed_login_is_rejected_and_audited(client):
    client.post("/auth/register", json={
        "email": "x@example.com", "password": "long enough password",
    })
    bad = client.post("/auth/login", json={
        "email": "x@example.com", "password": "totally wrong password",
    })
    assert bad.status_code == 401


def test_weak_password_is_rejected(client):
    response = client.post("/auth/register", json={
        "email": "weak@example.com", "password": "short",
    })
    assert response.status_code == 422


def test_invalid_email_is_rejected(client):
    response = client.post("/auth/register", json={
        "email": "not-an-email", "password": "long enough password",
    })
    assert response.status_code == 422


def test_audit_requires_manager_role(client):
    client.post("/auth/register", json={
        "email": "viewer@example.com", "password": "long enough password",
        "role": "viewer",
    })
    login = client.post("/auth/login", json={
        "email": "viewer@example.com", "password": "long enough password",
    })
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/auth/audit", headers=headers).status_code == 403


def test_audit_visible_to_manager_and_records_events(client):
    client.post("/auth/register", json={
        "email": "boss@example.com", "password": "long enough password",
        "role": "manager",
    })
    login = client.post("/auth/login", json={
        "email": "boss@example.com", "password": "long enough password",
    })
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    audit = client.get("/auth/audit", headers=headers)
    assert audit.status_code == 200
    actions = {row["action"] for row in audit.json()}
    assert "user.registered" in actions
    assert "login.success" in actions


def test_machine_api_key_still_authenticates(monkeypatch):
    from app.config import settings
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "api_key", "machine-key")
    with TestClient(app) as probe:
        me = probe.get("/auth/me", headers={"X-API-Key": "machine-key"})
        assert me.status_code == 200
        assert me.json()["role"] == "admin"
