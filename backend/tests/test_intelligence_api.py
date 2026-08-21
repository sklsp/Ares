"""HTTP contracts for persisted intelligence objects."""

from __future__ import annotations

from datetime import timedelta

from app.db.models import Opportunity, OpportunityEvidence, ResearchJob, utcnow
from app.services.intelligence_jobs import recover_stale_jobs, wait_for_jobs


def test_tools_include_market_research(client):
    names = {tool["name"] for tool in client.get("/tools").json()}
    assert {"research_market", "discover_stores", "crawl_website", "list_opportunities"} <= names


def test_startup_recovery_requeues_orphaned_jobs(client, db):
    orphan = ResearchJob(
        objective="Interrupted investigation",
        query="fitness",
        status="RUNNING",
        stage="crawling",
        started_at=utcnow() - timedelta(minutes=1),
    )
    db.add(orphan)
    db.commit()

    recovered = recover_stale_jobs(db)
    assert recovered == 1
    assert wait_for_jobs(timeout=10)
    db.expire_all()
    refreshed = db.get(ResearchJob, orphan.id)
    assert refreshed.status in {"QUEUED", "COMPLETED", "FAILED"}
    assert refreshed.error is None or refreshed.stage != "crawling"


def test_startup_recovery_fails_permanently_stale_jobs(client, db):
    stale = ResearchJob(
        objective="Long-dead investigation",
        query="fitness",
        status="RUNNING",
        stage="crawling",
        started_at=utcnow() - timedelta(hours=2),
    )
    db.add(stale)
    db.commit()

    recover_stale_jobs(db)
    assert wait_for_jobs(timeout=10)
    db.expire_all()
    refreshed = db.get(ResearchJob, stale.id)
    assert refreshed.status == "FAILED"
    assert refreshed.error


def test_opportunity_detail_and_provenance_are_queryable(client, db):
    job = ResearchJob(objective="Fitness scan", query="fitness", status="COMPLETED", stage="complete")
    db.add(job)
    db.flush()
    opportunity = Opportunity(
        research_job_id=job.id,
        type="PRICING_OPPORTUNITY",
        title="Price spread",
        summary="Observed price spread across public stores.",
        recommended_action="Compare the products before taking action.",
        evidence={"observed": True},
        source_urls=["https://example.com/product"],
        score=72,
        confidence=.8,
        competition_level="moderate",
        demand_signals={"stores": 3},
    )
    db.add(opportunity)
    db.flush()
    db.add(OpportunityEvidence(opportunity_id=opportunity.id, source_url="https://example.com/product", source_domain="example.com", claim="Price was visible", observed_value={"price": 29}, confidence=.9))
    db.commit()

    response = client.get(f"/intelligence/opportunities/{opportunity.id}")
    assert response.status_code == 200
    assert response.json()["score"] == 72
    evidence = client.get(f"/intelligence/opportunities/{opportunity.id}/evidence")
    assert evidence.status_code == 200
    assert evidence.json()[0]["source_domain"] == "example.com"
    assert "_sa_instance_state" not in evidence.text
