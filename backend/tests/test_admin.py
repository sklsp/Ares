"""Admin user management: authorization, tenancy, and escalation guards."""

from __future__ import annotations

import pytest

from app.db.identity import Organization, User
from app.services.auth import hash_password


@pytest.fixture
def org_with_users(db):
    org = Organization(name="Admin Org")
    other = Organization(name="Other Org")
    db.add_all([org, other])
    db.flush()

    admin = User(organization_id=org.id, email="admin@org.com",
                 password_hash=hash_password("long enough password"), role="admin")
    viewer = User(organization_id=org.id, email="viewer@org.com",
                  password_hash=hash_password("long enough password"), role="viewer")
    analyst = User(organization_id=org.id, email="analyst@org.com",
                   password_hash=hash_password("long enough password"), role="analyst")
    second_admin = User(organization_id=org.id, email="admin2@org.com",
                        password_hash=hash_password("long enough password"), role="admin")
    foreign_admin = User(organization_id=other.id, email="foreign@other.com",
                         password_hash=hash_password("long enough password"), role="admin")
    db.add_all([admin, viewer, analyst, second_admin, foreign_admin])
    db.commit()

    def login(email: str) -> dict[str, str]:
        response = client_login(email)
        return {"Authorization": f"Bearer {response}"}

    def client_login(email: str) -> str:
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as probe:
            response = probe.post("/auth/login", json={
                "email": email, "password": "long enough password",
            })
            return response.json()["access_token"]

    return {
        "org": org, "other": other,
        "admin": admin, "viewer": viewer, "analyst": analyst,
        "second_admin": second_admin, "foreign_admin": foreign_admin,
        "admin_headers": {"Authorization": f"Bearer {client_login('admin@org.com')}"},
        "viewer_headers": {"Authorization": f"Bearer {client_login('viewer@org.com')}"},
        "analyst_headers": {"Authorization": f"Bearer {client_login('analyst@org.com')}"},
        "foreign_headers": {"Authorization": f"Bearer {client_login('foreign@other.com')}"},
    }


def test_viewer_cannot_access_admin_api(client, org_with_users):
    response = client.get("/admin/users", headers=org_with_users["viewer_headers"])
    assert response.status_code == 403


def test_analyst_cannot_access_admin_api(client, org_with_users):
    response = client.get("/admin/users", headers=org_with_users["analyst_headers"])
    assert response.status_code == 403


def test_manager_cannot_access_admin_api(client, db, org_with_users):
    manager = User(organization_id=org_with_users["org"].id, email="mgr@org.com",
                   password_hash=hash_password("long enough password"), role="manager")
    db.add(manager)
    db.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as probe:
        token = probe.post("/auth/login", json={
            "email": "mgr@org.com", "password": "long enough password",
        }).json()["access_token"]
    assert client.get("/admin/users",
                      headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_admin_lists_only_own_organization(client, org_with_users):
    response = client.get("/admin/users", headers=org_with_users["admin_headers"])
    assert response.status_code == 200
    emails = {user["email"] for user in response.json()}
    assert "foreign@other.com" not in emails
    assert "admin@org.com" in emails


def test_admin_cannot_modify_cross_tenant_user(client, org_with_users):
    foreign_id = org_with_users["foreign_admin"].id
    response = client.patch(f"/admin/users/{foreign_id}", json={"role": "viewer"},
                            headers=org_with_users["admin_headers"])
    assert response.status_code == 404


def test_cross_tenant_404_is_generic(client, org_with_users):
    missing = client.get("/admin/users/999999", headers=org_with_users["admin_headers"])
    # PATCH is the only detail route; verify via PATCH on a nonexistent id.
    missing_patch = client.patch("/admin/users/999999", json={"role": "viewer"},
                                 headers=org_with_users["admin_headers"])
    foreign_patch = client.patch(
        f"/admin/users/{org_with_users['foreign_admin'].id}", json={"role": "viewer"},
        headers=org_with_users["admin_headers"],
    )
    assert missing_patch.status_code == foreign_patch.status_code == 404
    assert missing_patch.json()["detail"] == foreign_patch.json()["detail"]


def test_last_admin_cannot_be_demoted(client, db, org_with_users):
    """Demote the second admin first; then the last one must be protected."""
    headers = org_with_users["admin_headers"]
    second_id = org_with_users["second_admin"].id
    ok = client.patch(f"/admin/users/{second_id}", json={"role": "viewer"}, headers=headers)
    assert ok.status_code == 200

    last_admin_id = org_with_users["admin"].id
    denied = client.patch(f"/admin/users/{last_admin_id}", json={"role": "viewer"},
                          headers=headers)
    assert denied.status_code == 409
    db.expire_all()
    assert db.get(User, last_admin_id).role == "admin"


def test_last_admin_cannot_be_deactivated(client, db, org_with_users):
    headers = org_with_users["admin_headers"]
    # Remove the second admin's power first so only one active admin remains.
    ok = client.patch(f"/admin/users/{org_with_users['second_admin'].id}",
                      json={"role": "viewer"}, headers=headers)
    assert ok.status_code == 200

    admin_id = org_with_users["admin"].id
    response = client.patch(f"/admin/users/{admin_id}", json={"is_active": False},
                            headers=headers)
    assert response.status_code == 409
    db.expire_all()
    assert db.get(User, admin_id).is_active is True


def test_deactivating_one_of_two_admins_is_allowed(client, db, org_with_users):
    """With two active admins, removing one is legitimate."""
    headers = org_with_users["admin_headers"]
    response = client.patch(f"/admin/users/{org_with_users['second_admin'].id}",
                            json={"is_active": False}, headers=headers)
    assert response.status_code == 200
    db.expire_all()
    assert db.get(User, org_with_users["admin"].id).is_active is True


def test_admin_can_demote_when_another_admin_remains(client, db, org_with_users):
    headers = org_with_users["admin_headers"]
    response = client.patch(f"/admin/users/{org_with_users['second_admin'].id}",
                            json={"role": "analyst"}, headers=headers)
    assert response.status_code == 200
    db.expire_all()
    assert db.get(User, org_with_users["second_admin"].id).role == "analyst"


def test_deactivation_revokes_live_sessions(client, org_with_users):
    headers = org_with_users["viewer_headers"]
    # The viewer's session is live: /auth/me works.
    assert client.get("/auth/me", headers=headers).status_code == 200

    admin_headers = org_with_users["admin_headers"]
    response = client.patch(f"/admin/users/{org_with_users['viewer'].id}",
                            json={"is_active": False}, headers=admin_headers)
    assert response.status_code == 200

    # The revoked session is rejected immediately.
    assert client.get("/auth/me", headers=headers).status_code == 401


def test_role_changes_are_audited(client, db, org_with_users):
    from app.db.identity import AuditLog

    client.patch(f"/admin/users/{org_with_users['second_admin'].id}",
                 json={"role": "manager"}, headers=org_with_users["admin_headers"])
    logs = db.query(AuditLog).filter(AuditLog.action == "user.updated").all()
    assert logs
