"""Deterministic tests for responsible crawling and opportunity persistence."""

from __future__ import annotations

from app.db.models import Opportunity, ResearchJob
from app.intelligence.crawler import CrawlPolicy, CrawlResult, ResponsibleCrawler
from app.intelligence.extraction import ExtractedProduct, extract_page
from app.services.intelligence import run_investigation


class FakeSearch:
    def search(self, query: str, *, limit: int = 10) -> list[str]:
        return ["https://one.example", "https://two.example"]

    def close(self) -> None:
        pass


class FakeCrawler:
    def crawl(self, urls: list[str]) -> list[CrawlResult]:
        return [
            CrawlResult(
                "https://one.example/p/foam-roller",
                200,
                [ExtractedProduct(name="Recovery Foam Roller", price=29, currency="USD", url="https://one.example/p/foam-roller", confidence=.95)],
                [],
                {"title": "One Fitness"},
            ),
            CrawlResult(
                "https://two.example/p/foam-roller",
                200,
                [ExtractedProduct(name="Recovery Foam Roller", price=35, currency="USD", url="https://two.example/p/foam-roller", confidence=.95)],
                [],
                {"title": "Two Fitness"},
            ),
        ]

    def close(self) -> None:
        pass


def test_extracts_json_ld_product_and_metadata():
    html = '''<html><head><meta property="og:title" content="Roller"><script type="application/ld+json">{"@type":"Product","name":"Recovery Roller","brand":{"name":"Move"},"offers":{"price":29.5,"priceCurrency":"USD","availability":"https://schema.org/InStock"}}</script></head></html>'''
    products, links, metadata = extract_page(html, "https://example.com/product")
    assert links == []
    assert metadata["title"] == ""
    assert products[0].name == "Recovery Roller"
    assert products[0].price == 29.5
    assert products[0].availability == "instock"
    assert products[0].confidence == .95


def test_crawler_blocks_disallowed_robots():
    class Client:
        def get(self, url):
            class Response:
                status_code = 200
                text = "User-agent: *\nDisallow: /"
            return Response()

    # Opt past the SSRF gate; this test targets robots handling specifically.
    crawler = ResponsibleCrawler(
        CrawlPolicy(delay_seconds=0, allow_private_addresses=True), client=Client()
    )
    result = crawler.fetch("https://blocked.example/product")
    assert result.robots_allowed is False
    assert result.error == "Blocked by robots.txt"


def test_crawler_blocks_private_and_non_http_destinations():
    class Client:
        def get(self, url):  # pragma: no cover - must never be reached
            raise AssertionError("crawler attempted a disallowed destination")

    crawler = ResponsibleCrawler(CrawlPolicy(delay_seconds=0), client=Client())
    for url in (
        "http://127.0.0.1:8765/secret",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/internal",
        "file:///etc/passwd",
        "ftp://example.invalid/file",
    ):
        result = crawler.fetch(url)
        assert result.error and "Blocked" in result.error, url
        assert result.status_code is None


def test_crawler_allows_private_destinations_only_when_opted_in():
    class Client:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def get(self, url):
            self.requested.append(url)

            class Response:
                status_code = 200
                text = "User-agent: *\nAllow: /"
                content = b"<html><head><title>ok</title></head></html>"
                encoding = "utf-8"
                url = "http://127.0.0.1:8765/fixture.html"

                def raise_for_status(self) -> None:
                    return None

            return Response()

    client = Client()
    crawler = ResponsibleCrawler(
        CrawlPolicy(delay_seconds=0, allow_private_addresses=True), client=client
    )
    result = crawler.fetch("http://127.0.0.1:8765/fixture.html")
    assert result.error is None
    assert result.robots_allowed is True


def test_investigation_persists_evidence_and_assortment_gap(db):
    job = ResearchJob(objective="Find fitness opportunities", query="fitness")
    db.add(job)
    db.commit()
    db.refresh(job)

    run_investigation(db, job, search=FakeSearch(), crawler=FakeCrawler())

    opportunity = db.query(Opportunity).one()
    assert opportunity.type == "ASSORTMENT_GAP"
    assert opportunity.score > 50
    assert len(opportunity.source_urls) == 2
    evidence = db.execute(__import__("sqlalchemy").select(__import__("app.db.models", fromlist=["OpportunityEvidence"]).OpportunityEvidence)).scalars().all()
    assert len(evidence) == 2
    assert job.status == "COMPLETED"
    assert job.stats["products_discovered"] == 2
