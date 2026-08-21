"""HTTP contracts for persisted intelligence objects."""

from __future__ import annotations

from app.db.models import Opportunity, OpportunityEvidence, ResearchJob


def test_tools_include_market_research(client):
    names = {tool["name"] for tool in client.get("/tools").json()}
    assert {"research_market", "list_opportunities"} <= names


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
