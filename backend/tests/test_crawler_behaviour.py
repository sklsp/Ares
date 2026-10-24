"""Behavioural tests for the responsible crawler's edge paths.

These cover the branches that the persistence-focused suite in
test_intelligence.py does not reach: hostname-less URLs, DNS failures, a
resolved public address, robots.txt missing or unreachable, result caching,
per-host rate limiting, retry/backoff on request failure, and breadth-first
crawl traversal with depth limiting and de-duplication.

No network is used: HTTP goes through httpx.MockTransport and name resolution
is monkeypatched. time.sleep and time.monotonic are replaced so no test waits.
"""

from __future__ import annotations

import socket

import httpx

from app.intelligence.crawler import CrawlPolicy, ResponsibleCrawler, _is_public_http_url

PRODUCT_HTML = (
    '<html><head><title>Roller</title>'
    '<script type="application/ld+json">'
    '{"@type":"Product","name":"Recovery Roller",'
    '"offers":{"price":29.5,"priceCurrency":"USD"}}'
    '</script></head><body></body></html>'
)

SHARED_HTML = (
    '<html><head><title>Shared</title>'
    '<script type="application/ld+json">'
    '{"@type":"Product","name":"Shared Product",'
    '"offers":{"price":10,"priceCurrency":"USD"}}'
    '</script></head><body><a href="/deep">Deep</a></body></html>'
)

ALLOW_ALL_ROBOTS = "User-agent: *\nAllow: /"


class FakeClock:
    """A controllable monotonic clock so rate-limit waits are exact."""

    def __init__(self, start: float) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _crawler(handler, **policy_kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    kwargs = {"delay_seconds": 0, "allow_private_addresses": True}
    kwargs.update(policy_kwargs)
    return ResponsibleCrawler(CrawlPolicy(**kwargs), client=client)


def test_is_public_http_url_rejects_url_without_hostname():
    # http scheme with an empty netloc has no hostname to resolve.
    assert _is_public_http_url("http:///no-host") is False


def test_is_public_http_url_rejects_unresolvable_host(monkeypatch):
    def boom(host, port):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr("socket.getaddrinfo", boom)
    assert _is_public_http_url("https://unresolvable.invalid/") is False


def test_is_public_http_url_accepts_resolved_public_address(monkeypatch):
    infos = [("AF_INET", "SOCK_STREAM", 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port: infos)
    assert _is_public_http_url("https://example.com/") is True


def test_fetch_treats_missing_robots_as_allowed():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, content=PRODUCT_HTML.encode())

    crawler = _crawler(handler)
    result = crawler.fetch("https://shop.example/roller")
    assert result.error is None
    assert result.robots_allowed is True
    assert result.products[0].name == "Recovery Roller"


def test_fetch_treats_robots_error_as_allowed():
    def handler(request):
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("robots unreachable")
        return httpx.Response(200, content=PRODUCT_HTML.encode())

    crawler = _crawler(handler)
    result = crawler.fetch("https://shop.example/roller")
    assert result.error is None
    assert result.robots_allowed is True


def test_fetch_returns_cached_result_without_second_request():
    hits = {"page": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        hits["page"] += 1
        return httpx.Response(200, content=PRODUCT_HTML.encode())

    crawler = _crawler(handler)
    first = crawler.fetch("https://shop.example/roller")
    second = crawler.fetch("https://shop.example/roller")
    assert hits["page"] == 1
    assert first.products[0].name == "Recovery Roller"
    assert second.products[0].name == "Recovery Roller"


def test_fetch_rate_limits_requests_to_same_host(monkeypatch):
    clock = FakeClock(start=100.0)
    sleeps: list[float] = []
    monkeypatch.setattr("time.monotonic", clock.monotonic)
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        return httpx.Response(200, content=PRODUCT_HTML.encode())

    crawler = _crawler(handler, delay_seconds=1.0)
    crawler.fetch("https://shop.example/a")  # first request to host: no wait
    clock.advance(0.5)                        # now 100.5, only 0.5s since last
    crawler.fetch("https://shop.example/b")   # waits the remaining 0.5s

    assert sleeps == [0.5]


def test_fetch_retries_then_reports_last_error(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        raise httpx.ConnectError("boom")

    crawler = _crawler(handler, max_retries=1)
    result = crawler.fetch("https://shop.example/roller")
    assert result.error == "boom"
    assert result.status_code is None
    # one backoff (2**0 = 1s); the final attempt does not sleep after it.
    assert sleeps == [1]


def test_crawl_breadth_first_follows_links_and_respects_depth():
    pages = {
        "/": '<html><head><title>Home</title></head><body>'
             '<a href="/p/a">A</a><a href="/p/b">B</a></body></html>',
        "/p/a": '<html><head><title>A</title></head><body><a href="/shared">Shared</a></body></html>',
        "/p/b": '<html><head><title>B</title></head><body><a href="/shared">Shared</a></body></html>',
        "/shared": SHARED_HTML,
    }

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ALLOW_ALL_ROBOTS)
        html = pages.get(request.url.path)
        if html is None:
            return httpx.Response(404, text="nope")
        return httpx.Response(200, content=html.encode())

    crawler = _crawler(handler, max_depth=2)
    results = crawler.crawl(["https://shop.example/"])

    assert [r.url for r in results] == [
        "https://shop.example/",
        "https://shop.example/p/a",
        "https://shop.example/p/b",
        "https://shop.example/shared",
    ]
    assert results[3].products[0].name == "Shared Product"
    # /deep is linked from /shared (depth 2) but must not be crawled at max_depth=2.
    assert all(not r.url.endswith("/deep") for r in results)

