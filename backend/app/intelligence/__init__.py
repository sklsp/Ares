"""Responsible web intelligence primitives and opportunity analysis."""

from app.intelligence.crawler import CrawlPolicy, CrawlResult, ResponsibleCrawler
from app.intelligence.extraction import ExtractedProduct, extract_page

__all__ = [
    "CrawlPolicy",
    "CrawlResult",
    "ResponsibleCrawler",
    "ExtractedProduct",
    "extract_page",
]
