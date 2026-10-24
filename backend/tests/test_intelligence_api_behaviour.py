"""Behavioural tests for the market-intelligence HTTP API.

Covers the endpoints in app/api/intelligence.py that the contract suite does not
reach: cancelling a job, listing stores with grouped product counts, listing
external products (with and without search), and filtering opportunities by kind.
Every test asserts an observable value; no network or real worker is involved.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.identity import Organization, User
from app.db.models import (
    ExternalProduct,
    ExternalStore,
    Opportunity,
    ResearchJob,
    utcnow,
)
from app.services.auth import hash_password


@pytest.fixture
def manager(client, db):
    """An authenticated manager plus the organization that owns its fixtures."""
    org = Organization(name="Intel API Behaviour Org")
    db.add(org)
    db.flush()
    user = User(organization_id=org.id, email="intel-api-behaviour@example.com",
                password_hash=hash_password("long enough password"), role="manager")
    db.add(user)
    db.commit()

    login = client.post("/auth/login", json={
        "email": "intel-api-behaviour@example.com",
        "password": "long enough password",
    })
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, org


def _store(db, *, domain, name="", niche="", platform="unknown", country="",
           organization_id=None, last_crawled_at=None):
    row = ExternalStore(
        domain=domain, name=name, niche=niche, platform=platform, country=country,
        organization_id=organization_id, last_crawled_at=last_crawled_at,
    )
    db.add(row)
    return row


def _product(db, *, store, source_url, name, normalized_name, price=None, currency="",
             availability="unknown", confidence=0.0, last_seen_at=None):
    row = ExternalProduct(
        store_id=store.id, source_url=source_url, name=name,
        normalized_name=normalized_name, price=price, currency=currency,
        availability=availability, confidence=confidence,
    )
    if last_seen_at is not None:
        row.last_seen_at = last_seen_at
    db.add(row)
    return row


def _opportunity(db, *, organization_id, type_, title, score):
    row = Opportunity(
        organization_id=organization_id, type=type_, title=title,
        summary=f"Summary for {title}", recommended_action="Review the signal",
        evidence={"signal": "demand"}, source_urls=["https://example.com/evidence"],
        score=score, confidence=.5,
    )
    db.add(row)
    return row


# --- POST /jobs/{id}/cancel ---------------------------------------------------

def test_cancel_queued_job_marks_it_cancelled(client, db, manager):
    headers, org = manager
    job = ResearchJob(objective="Cancel me", query="trail shoes",
                      status="QUEUED", stage="queued", organization_id=org.id)
    db.add(job)
    db.commit()

    response = client.post(f"/intelligence/jobs/{job.id}/cancel", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == job.id
    assert body["status"] == "CANCELLED"


def test_cancel_completed_job_is_a_noop(client, db, manager):
    headers, org = manager
    job = ResearchJob(objective="Already done", query="yoga mats",
                      status="COMPLETED", stage="complete", organization_id=org.id)
    db.add(job)
    db.commit()

    response = client.post(f"/intelligence/jobs/{job.id}/cancel", headers=headers)

    assert response.status_code == 200
    # A terminal job cannot be cancelled; it is returned unchanged.
    assert response.json()["status"] == "COMPLETED"


def test_cancel_other_tenant_job_returns_404(client, db, manager):
    headers, org = manager
    other_org = Organization(name="Other Org")
    db.add(other_org)
    db.flush()
    job = ResearchJob(objective="Foreign investigation", query="trail shoes",
                      status="QUEUED", organization_id=other_org.id)
    db.add(job)
    db.commit()

    response = client.post(f"/intelligence/jobs/{job.id}/cancel", headers=headers)

    # Cross-tenant access is a 404, not a 403: no existence leak.
    assert response.status_code == 404


# --- GET /stores --------------------------------------------------------------

def test_list_stores_reports_grouped_product_counts_and_ordering(client, db, manager):
    headers, org = manager
    now = utcnow()
    busy = _store(db, domain="busy.example", name="Busy Shop", niche="running",
                  organization_id=org.id, last_crawled_at=now)
    quiet = _store(db, domain="quiet.example", name="Quiet Shop", niche="yoga",
                   organization_id=org.id, last_crawled_at=now - timedelta(hours=1))
    db.flush()
    for i in range(3):
        _product(db, store=busy, source_url=f"https://busy.example/p{i}",
                 name=f"Busy Product {i}", normalized_name=f"busy product {i}")
    db.commit()

    response = client.get("/intelligence/stores", headers=headers)

    assert response.status_code == 200
    rows = response.json()
    # Most recently crawled store first.
    assert [row["domain"] for row in rows] == ["busy.example", "quiet.example"]
    by_domain = {row["domain"]: row for row in rows}
    # The grouped COUNT query, not one query per store: 3 vs the .get default of 0.
    assert by_domain["busy.example"]["product_count"] == 3
    assert by_domain["quiet.example"]["product_count"] == 0
    busy_row = by_domain["busy.example"]
    assert busy_row["name"] == "Busy Shop"
    assert busy_row["niche"] == "running"
    assert busy_row["platform"] == "unknown"


# --- GET /products ------------------------------------------------------------

def test_list_external_products_orders_by_last_seen(client, db):
    # No auth header: /products has no tenant dependency.
    store = _store(db, domain="catalog.example", organization_id=None)
    db.flush()
    now = utcnow()
    older = _product(db, store=store, source_url="https://catalog.example/old",
                     name="Old Item", normalized_name="old item",
                     last_seen_at=now - timedelta(hours=1))
    newer = _product(db, store=store, source_url="https://catalog.example/new",
                     name="New Item", normalized_name="new item",
                     last_seen_at=now)
    db.commit()

    response = client.get("/intelligence/products")

    assert response.status_code == 200
    rows = response.json()
    assert [row["name"] for row in rows] == ["New Item", "Old Item"]
    top = rows[0]
    assert top["source_url"] == "https://catalog.example/new"
    assert top["store_id"] == store.id


def test_list_external_products_filters_by_search_case_insensitively(client, db):
    store = _store(db, domain="catalog.example", organization_id=None)
    db.flush()
    _product(db, store=store, source_url="https://catalog.example/a",
             name="Trail Runner", normalized_name="trail runner")
    _product(db, store=store, source_url="https://catalog.example/b",
             name="Yoga Mat", normalized_name="yoga mat")
    db.commit()

    response = client.get("/intelligence/products", params={"search": "TRAIL"})

    assert response.status_code == 200
    rows = response.json()
    # The search term is lowercased before matching normalized_name.
    assert [row["name"] for row in rows] == ["Trail Runner"]

    none = client.get("/intelligence/products", params={"search": "zzz"})
    assert none.status_code == 200
    assert none.json() == []


# --- GET /opportunities?kind= -------------------------------------------------

def test_list_opportunities_filters_by_kind(client, db, manager):
    headers, org = manager
    _opportunity(db, organization_id=org.id, type_="PRICING_OPPORTUNITY",
                 title="Price spread", score=.9)
    _opportunity(db, organization_id=org.id, type_="DEMAND_GAP",
                 title="Demand gap", score=.6)
    db.commit()

    response = client.get("/intelligence/opportunities",
                          params={"kind": "DEMAND_GAP"}, headers=headers)

    assert response.status_code == 200
    rows = response.json()
    assert [row["title"] for row in rows] == ["Demand gap"]
    assert rows[0]["type"] == "DEMAND_GAP"

    # Without a kind filter both are returned, ranked by score descending.
    all_rows = client.get("/intelligence/opportunities", headers=headers).json()
    assert [row["title"] for row in all_rows] == ["Price spread", "Demand gap"]
