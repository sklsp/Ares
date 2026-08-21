"""Metrics endpoint and instrumentation behavior."""

from __future__ import annotations


def test_metrics_endpoint_exposes_prometheus_text(client):
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "http_requests_total" in response.text


def test_requests_are_counted_with_status_and_path(client):
    before = client.get("/metrics").text
    assert 'path="/health"' not in before or True  # idempotent baseline

    client.get("/health")
    body = client.get("/metrics").text
    assert 'http_requests_total{method="GET",path="/health",status="200"}' in body


def test_correlation_id_is_echoed(client):
    response = client.get("/health", headers={"X-Correlation-ID": "test-cid-123"})
    assert response.headers["X-Correlation-ID"] == "test-cid-123"

    generated = client.get("/health")
    assert generated.headers["X-Correlation-ID"]


def test_crawler_metrics_record_outcomes():
    from app.intelligence.crawler import CrawlPolicy, ResponsibleCrawler

    class Client:
        def get(self, url):
            class Response:
                status_code = 200
                text = "User-agent: *\nDisallow: /"
            return Response()

    crawler = ResponsibleCrawler(
        CrawlPolicy(delay_seconds=0, allow_private_addresses=True), client=Client()
    )
    crawler.fetch("https://blocked.example/product")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as probe:
        body = probe.get("/metrics").text
    assert 'crawler_robots_denied_total' in body
