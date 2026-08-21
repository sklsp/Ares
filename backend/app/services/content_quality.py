"""Deterministic product-content scoring.

The agent needs a *stable* signal for "which descriptions are weak" - if that
judgement came from the LLM the same question would return different products
every run, and the ranking could not be tested. So scoring is plain Python and
the LLM is only used for the creative part (writing the replacement copy).

Score is 0-100, built from five weighted components.
"""

from __future__ import annotations

import re

from app.schemas.domain import ContentAnalysis, Grade

# Component weights (must sum to 100).
W_DESCRIPTION_LENGTH = 35
W_TITLE = 20
W_SPECIFICS = 20
W_USE_CASE = 15
W_ORIGINALITY = 10

SPEC_KEYWORDS = (
    "material",
    "cotton",
    "polyester",
    "aluminium",
    "aluminum",
    "steel",
    "leather",
    "rubber",
    "foam",
    "weight",
    "size",
    "capacity",
    "battery",
    "bluetooth",
    "waterproof",
    "washable",
    "warranty",
    "dimensions",
    "gram",
    "kg",
    "ml",
    "mm",
    "cm",
    "hours",
    "watt",
)

USE_CASE_KEYWORDS = (
    "ideal for",
    "perfect for",
    "designed for",
    "great for",
    "built for",
    "suitable for",
    "whether you",
    "use it",
    "helps you",
    "so you can",
    "keeps you",
    "for everyday",
    "for training",
    "for daily",
)

FILLER_PHRASES = (
    "high quality product",
    "great product",
    "good product",
    "nice product",
    "best product",
    "you will love it",
    "buy now",
    "amazing quality",
    "top quality",
    "must have",
)

PLACEHOLDER_TITLES = ("todo", "new product", "untitled", "test", "product name")

GRADE_THRESHOLDS: tuple[tuple[int, Grade], ...] = (
    (80, "excellent"),
    (60, "good"),
    (40, "weak"),
    (0, "poor"),
)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9'-]+", text or "")


def grade_for(score: int) -> Grade:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "poor"


def score_content(title: str, description: str) -> tuple[int, list[str], list[str], int, int]:
    """Return (score, issues, suggestions, description_word_count, title_word_count)."""
    title = (title or "").strip()
    description = (description or "").strip()
    lower_desc = description.lower()

    desc_words = _words(description)
    title_words = _words(title)
    n_desc, n_title = len(desc_words), len(title_words)

    issues: list[str] = []
    suggestions: list[str] = []
    score = 0

    # 1. Description length -------------------------------------------------
    if n_desc == 0:
        issues.append("Description is empty")
        suggestions.append("Write a description of at least 60 words")
    elif n_desc < 15:
        score += 8
        issues.append(f"Description is very short ({n_desc} words)")
        suggestions.append("Expand the description to at least 60 words")
    elif n_desc < 30:
        score += 18
        issues.append(f"Description is thin ({n_desc} words)")
        suggestions.append("Add a second paragraph covering materials and care")
    elif n_desc < 60:
        score += 28
    else:
        score += W_DESCRIPTION_LENGTH

    # 2. Title --------------------------------------------------------------
    title_score = W_TITLE
    if n_title == 0:
        title_score = 0
        issues.append("Title is empty")
    else:
        if n_title < 3:
            title_score -= 10
            issues.append(f"Title is too generic ({n_title} words)")
            suggestions.append("Extend the title with the model, material or key benefit")
        if title.isupper() and n_title > 1:
            title_score -= 5
            issues.append("Title is written in all caps")
            suggestions.append("Use sentence or title case instead of all caps")
        if any(p in title.lower() for p in PLACEHOLDER_TITLES):
            title_score = 0
            issues.append("Title looks like a placeholder")
            suggestions.append("Replace the placeholder title with the real product name")
    score += max(title_score, 0)

    # 3. Concrete specifics -------------------------------------------------
    has_numbers = bool(re.search(r"\d", description))
    keyword_hits = sum(1 for k in SPEC_KEYWORDS if k in lower_desc)
    specifics = 0
    if has_numbers:
        specifics += 6
    specifics += min(keyword_hits, 3) * 4
    if "\n-" in description or "\n•" in description or "* " in description:
        specifics += 2
    specifics = min(specifics, W_SPECIFICS)
    if specifics < 10:
        issues.append("Missing concrete details (materials, dimensions, specs)")
        suggestions.append("List the key specifications: material, size, weight, care")
    score += specifics

    # 4. Use cases / benefits ----------------------------------------------
    if any(k in lower_desc for k in USE_CASE_KEYWORDS):
        score += W_USE_CASE
    else:
        issues.append("No use case or target customer mentioned")
        suggestions.append("Say who the product is for and when they would use it")

    # 5. Originality --------------------------------------------------------
    filler_hits = [p for p in FILLER_PHRASES if p in lower_desc]
    if filler_hits:
        issues.append(f"Contains filler marketing phrases: {', '.join(filler_hits[:3])}")
        suggestions.append("Replace generic claims with verifiable product facts")
    else:
        score += W_ORIGINALITY

    score = max(0, min(100, score))
    return score, issues, suggestions, n_desc, n_title


def analyze_product(product) -> ContentAnalysis:
    """Score a Product ORM row (or anything with the same attributes)."""
    score, issues, suggestions, n_desc, n_title = score_content(
        product.title, product.description
    )
    return ContentAnalysis(
        product_id=product.id,
        sku=product.sku,
        title=product.title,
        score=score,
        grade=grade_for(score),
        issues=issues,
        suggestions=suggestions,
        description_word_count=n_desc,
        title_word_count=n_title,
    )
