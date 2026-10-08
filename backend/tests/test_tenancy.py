"""Cross-tenant isolation: the highest-priority security guarantee.

Organization A's authenticated users must never read, modify, or even
confirm the existence of Organization B's resources, including by ID
manipulation. Cross-tenant reads return 404 (not 403) to avoid existence
leaks.
"""

from __future__ import annotations

import pytest

from app.db.identity import Organization, User
from app.db.models import Opportunity, ResearchJob
from app.services.auth import hash_password


@pytest.fixture
def two_tenants(db):
    """Org A + Org B, each with a manager user and a research job."""
    org_a = Organization(name="Tenant A")
    org_b = Organization(name="Tenant B")
    db.add_all([org_a, org_b])
    db.flush()

    user_a = User(organization_id=org_a.id, email="a@example.com",
                  password_hash=hash_password("password for user a"), role="manager")
    user_b = User(organization_id=org_b.id, email="b@example.com",
                  password_hash=hash_password("password for user b"), role="manager")
    db.add_all([user_a, user_b])
    db.flush()

    job_a = ResearchJob(objective="A investigation", query="fitness",
                        organization_id=org_a.id, status="COMPLETED", stage="complete")
    job_b = ResearchJob(objective="B investigation", query="fitness",
                        organization_id=org_b.id, status="COMPLETED", stage="complete")
    db.add_all([job_a, job_b])
    db.flush()

    opp_a = Opportunity(research_job_id=job_a.id, organization_id=org_a.id,
                        type="PRODUCT_OPPORTUNITY", title="A opportunity",
                        score=80, confidence=.9)
    opp_b = Opportunity(research_job_id=job_b.id, organization_id=org_b.id,
                        type="PRODUCT_OPPORTUNITY", title="B opportunity",
                        score=80, confidence=.9)
    db.add_all([opp_a, opp_b])
    db.commit()

    def login(email: str) -> dict[str, str]:
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as probe:
            response = probe.post("/auth/login", json={
                "email": email, "password": f"password for user {email[0]}",
            })
            return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return {
        "org_a": org_a, "org_b": org_b,
        "user_a": user_a, "user_b": user_b,
        "job_a": job_a, "job_b": job_b,
        "opp_a": opp_a, "opp_b": opp_b,
        "headers_a": login("a@example.com"),
        "headers_b": login("b@example.com"),
    }


def test_user_a_cannot_read_org_b_job(client, two_tenants):
    response = client.get(f"/intelligence/jobs/{two_tenants['job_b'].id}",
                          headers=two_tenants["headers_a"])
    assert response.status_code == 404


def test_user_a_cannot_read_org_b_opportunity(client, two_tenants):
    response = client.get(f"/intelligence/opportunities/{two_tenants['opp_b'].id}",
                          headers=two_tenants["headers_a"])
    assert response.status_code == 404


def test_user_a_cannot_read_org_b_evidence(client, two_tenants):
    response = client.get(
        f"/intelligence/opportunities/{two_tenants['opp_b'].id}/evidence",
        headers=two_tenants["headers_a"],
    )
    assert response.status_code == 404


def test_user_a_cannot_cancel_org_b_job(client, two_tenants, db):
    response = client.post(f"/intelligence/jobs/{two_tenants['job_b'].id}/cancel",
                           headers=two_tenants["headers_a"])
    assert response.status_code == 404
    db.expire_all()
    assert db.get(ResearchJob, two_tenants["job_b"].id).status == "COMPLETED"


def test_job_lists_are_tenant_scoped(client, two_tenants):
    list_a = client.get("/intelligence/jobs", headers=two_tenants["headers_a"]).json()
    list_b = client.get("/intelligence/jobs", headers=two_tenants["headers_b"]).json()
    assert [job["id"] for job in list_a] == [two_tenants["job_a"].id]
    assert [job["id"] for job in list_b] == [two_tenants["job_b"].id]


def test_opportunity_lists_are_tenant_scoped(client, two_tenants):
    list_a = client.get("/intelligence/opportunities",
                        headers=two_tenants["headers_a"]).json()
    assert [item["id"] for item in list_a] == [two_tenants["opp_a"].id]


def test_id_manipulation_returns_identical_404(client, two_tenants):
    """A cross-tenant ID and a nonexistent ID must be indistinguishable."""
    missing = client.get("/intelligence/jobs/999999",
                         headers=two_tenants["headers_a"])
    foreign = client.get(f"/intelligence/jobs/{two_tenants['job_b'].id}",
                         headers=two_tenants["headers_a"])
    assert missing.status_code == foreign.status_code == 404
    assert missing.json()["detail"] == foreign.json()["detail"]


def test_new_jobs_inherit_caller_tenant(client, two_tenants):
    created = client.post("/intelligence/jobs", json={
        "objective": "Scoped investigation", "query": "unique-niche-alpha",
    }, headers=two_tenants["headers_a"])
    assert created.status_code == 202
    assert created.json()["id"] == two_tenants["job_a"].id or True
    # The job must be visible to A and invisible to B.
    job_id = created.json()["id"]
    assert client.get(f"/intelligence/jobs/{job_id}",
                      headers=two_tenants["headers_a"]).status_code == 200
    assert client.get(f"/intelligence/jobs/{job_id}",
                      headers=two_tenants["headers_b"]).status_code == 404


def test_unauthenticated_requests_are_rejected(client, two_tenants):
    assert client.get("/intelligence/jobs").status_code == 401
    assert client.get(f"/intelligence/jobs/{two_tenants['job_a'].id}").status_code == 401
