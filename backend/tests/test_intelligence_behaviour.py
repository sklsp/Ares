"""Behavioural tests for the market-intelligence agent tools."""

from __future__ import annotations

import pytest

from app.db.models import Opportunity, ResearchJob
from app.intelligence.crawler import CrawlResult
from app.intelligence.extraction import ExtractedProduct
from app.jobs.queue import job_signature
from app.services import intelligence_jobs
from app.tools import intelligence as intel_tools
from app.tools.registry import ToolError, ToolValidationError


def _install_fake_search(monkeypatch, urls=(), fail_with=None):
    """Replace DuckDuckGoProvider inside the tool module with a scripted fake."""
    calls = {"search": [], "closed": 0}

    class FakeSearchProvider:
        def search(self, query, *, limit=10):
            calls["search"].append((query, limit))
            if fail_with is not None:
                raise fail_with
            return list(urls)

        def close(self):
            calls["closed"] += 1

    monkeypatch.setattr(intel_tools, "DuckDuckGoProvider", FakeSearchProvider)
    return calls


def _install_fake_crawler(monkeypatch, results=(), fail_with=None):
    """Replace ResponsibleCrawler inside the tool module with a scripted fake."""
    calls = {"policies": [], "start_urls": None, "closed": 0}

    class FakeCrawler:
        def __init__(self, policy):
            calls["policies"].append(policy)

        def crawl(self, start_urls):
            calls["start_urls"] = list(start_urls)
            if fail_with is not None:
                raise fail_with
            return list(results)

        def close(self):
            calls["closed"] += 1

    monkeypatch.setattr(intel_tools, "ResponsibleCrawler", FakeCrawler)
    return calls


def _crawl_result(url, *, products=(), error=None, robots_allowed=True, status_code=200):
    return CrawlResult(
        url=url,
        status_code=status_code,
        products=[ExtractedProduct(name=name) for name in products],
        links=[],
        metadata={},
        error=error,
        robots_allowed=robots_allowed,
    )


def _seed_opportunity(db, *, title, type_, score):
    row = Opportunity(
        type=type_,
        title=title,
        summary=f"Summary for {title}",
        recommended_action="Check the niche",
        evidence={"signal": "demand"},
        source_urls=["https://example.com/evidence"],
        score=score,
    )
    db.add(row)
    return row


# --- discover_stores ---------------------------------------------------------

def test_discover_stores_returns_urls_and_deduped_domains(ctx, registry, monkeypatch):
    urls = [
        "https://shop-a.com/collections/all",
        "https://shop-b.com/",
        "https://shop-a.com/pages/about",
    ]
    calls = _install_fake_search(monkeypatch, urls=urls)

    out = registry.execute("discover_stores", {"query": "trail running shoes", "limit": 5}, ctx)

    assert out == {
        "query": "trail running shoes",
        "urls": urls,
        "domains": ["shop-a.com", "shop-b.com"],
    }
    assert calls["search"] == [("trail running shoes ecommerce store", 5)]
    assert calls["closed"] == 1


def test_discover_stores_omits_slashless_urls_from_domains(ctx, registry, monkeypatch):
    urls = ["https://plain.example", "not-a-url"]
    _install_fake_search(monkeypatch, urls=urls)

    out = registry.execute("discover_stores", {"query": "yoga mats"}, ctx)

    assert out["urls"] == urls
    assert out["domains"] == ["plain.example"]


def test_discover_stores_closes_provider_when_search_fails(ctx, registry, monkeypatch):
    calls = _install_fake_search(monkeypatch, fail_with=RuntimeError("search exploded"))

    with pytest.raises(ToolError, match="discover_stores failed: search exploded"):
        registry.execute("discover_stores", {"query": "trail running shoes"}, ctx)

    assert calls["closed"] == 1


def test_discover_stores_rejects_limit_out_of_bounds(ctx, registry):
    with pytest.raises(ToolValidationError, match="Invalid arguments for 'discover_stores'"):
        registry.execute("discover_stores", {"query": "yoga mats", "limit": 0}, ctx)
    with pytest.raises(ToolValidationError, match="Invalid arguments for 'discover_stores'"):
        registry.execute("discover_stores", {"query": "yoga mats", "limit": 26}, ctx)


# --- crawl_website -----------------------------------------------------------

def test_crawl_website_aggregates_pages_products_and_errors(ctx, registry, monkeypatch):
    results = [
        _crawl_result("https://a.example/", products=("Foam Roller", "Knee Sleeve")),
        _crawl_result("https://b.example/", products=("Mat",), error="forbidden",
                      robots_allowed=False, status_code=403),
        _crawl_result("https://c.example/"),
    ]
    calls = _install_fake_crawler(monkeypatch, results=results)

    out = registry.execute(
        "crawl_website",
        {"start_urls": ["https://a.example/", "https://b.example/", "https://c.example/"]},
        ctx,
    )

    assert out == {"pages": 3, "allowed_pages": 2, "products": 3, "errors": ["forbidden"]}
    policy = calls["policies"][0]
    assert (policy.max_pages, policy.max_depth, policy.delay_seconds) == (20, 1, .5)
    assert calls["start_urls"] == ["https://a.example/", "https://b.example/", "https://c.example/"]
    assert calls["closed"] == 1


def test_crawl_website_closes_crawler_when_crawl_fails(ctx, registry, monkeypatch):
    calls = _install_fake_crawler(monkeypatch, fail_with=RuntimeError("crawl exploded"))

    with pytest.raises(ToolError, match="crawl_website failed: crawl exploded"):
        registry.execute("crawl_website", {"start_urls": ["https://a.example/"]}, ctx)

    assert calls["closed"] == 1


def test_crawl_website_requires_at_least_one_start_url(ctx, registry):
    with pytest.raises(ToolValidationError, match="Invalid arguments for 'crawl_website'"):
        registry.execute("crawl_website", {"start_urls": []}, ctx)


# --- research_market ---------------------------------------------------------

@pytest.fixture
def no_worker(monkeypatch):
    """Keep the inline transport from executing the job inside create_job."""
    executed = []
    monkeypatch.setattr(intelligence_jobs, "execute_job", lambda job_id: executed.append(job_id))
    return executed


def test_research_market_queues_new_job(ctx, registry, no_worker):
    out = registry.execute(
        "research_market",
        {"objective": "Find trail running niches", "query": "trail running shoes",
         "start_urls": ["https://shop-a.com"]},
        ctx,
    )

    assert out["status"] == "QUEUED"
    assert out["message"] == "Research queued; inspect the job and opportunities for evidence-backed results."
    row = ctx.db.get(ResearchJob, out["job_id"])
    assert row.objective == "Find trail running niches"
    assert row.query == "trail running shoes"
    # start_urls is not a column; it must flow into the idempotency signature.
    assert row.idempotency_key == job_signature(
        "Find trail running niches", "trail running shoes", ["https://shop-a.com"]
    )


def test_research_market_returns_existing_job_for_identical_request(ctx, registry, no_worker):
    args = {"objective": "Find yoga mat niches", "query": "yoga mats"}

    first = registry.execute("research_market", dict(args), ctx)
    second = registry.execute("research_market", dict(args), ctx)

    assert second["job_id"] == first["job_id"]
    assert second["message"] == "An identical investigation is already queued or running; returning that job."
    assert ctx.db.query(ResearchJob).count() == 1

    # Different start_urls are a different investigation, not a duplicate.
    different = registry.execute("research_market", {**args, "start_urls": ["https://other.example"]}, ctx)
    assert different["job_id"] != first["job_id"]
    assert ctx.db.query(ResearchJob).count() == 2


# --- list_opportunities ------------------------------------------------------

def test_list_opportunities_returns_rows_ranked_by_score(ctx, registry, db):
    _seed_opportunity(db, title="Trail shoe gap", type_="gap", score=.9)
    _seed_opportunity(db, title="Yoga mat niche", type_="niche", score=.5)
    _seed_opportunity(db, title="Recovery gear gap", type_="gap", score=.7)
    db.commit()

    out = registry.execute("list_opportunities", {}, ctx)

    assert out["count"] == 3
    titles = [op["title"] for op in out["opportunities"]]
    scores = [op["score"] for op in out["opportunities"]]
    assert titles == ["Trail shoe gap", "Recovery gear gap", "Yoga mat niche"]
    assert scores == [.9, .7, .5]
    top = out["opportunities"][0]
    assert top["type"] == "gap"
    assert top["status"] == "NEW"
    assert top["evidence"] == {"signal": "demand"}
    assert top["source_urls"] == ["https://example.com/evidence"]


def test_list_opportunities_filters_by_kind(ctx, registry, db):
    _seed_opportunity(db, title="Trail shoe gap", type_="gap", score=.9)
    _seed_opportunity(db, title="Yoga mat niche", type_="niche", score=.5)
    db.commit()

    out = registry.execute("list_opportunities", {"kind": "niche"}, ctx)

    assert out["count"] == 1
    assert [op["title"] for op in out["opportunities"]] == ["Yoga mat niche"]

    empty = registry.execute("list_opportunities", {"kind": "nope"}, ctx)
    assert empty == {"count": 0, "opportunities": []}


def test_list_opportunities_respects_limit(ctx, registry, db):
    for i in range(3):
        _seed_opportunity(db, title=f"Opp {i}", type_="gap", score=float(i))
    db.commit()

    out = registry.execute("list_opportunities", {"limit": 2}, ctx)

    assert out["count"] == 2
    assert [op["title"] for op in out["opportunities"]] == ["Opp 2", "Opp 1"]
