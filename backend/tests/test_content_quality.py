"""The scoring service is what ranks 'worst products', so it gets its own tests."""

from __future__ import annotations

import pytest

from app.services.content_quality import grade_for, score_content

GOOD = (
    "Velocity Pro 5 Road Running Shoe",
    "The Velocity Pro 5 is a daily trainer for runners logging 40 to 80 km a week. "
    "A 32 mm nitrogen-infused foam midsole returns energy on long efforts while the "
    "8 mm drop keeps your stride familiar. The engineered mesh upper weighs 249 g in "
    "a size 9 and dries quickly after wet runs. Ideal for tempo sessions and weekend "
    "long runs on tarmac. Machine washable at 30 degrees, air dry only.",
)


def test_strong_copy_scores_high():
    score, issues, _, words, _ = score_content(*GOOD)
    assert score >= 80
    assert grade_for(score) == "excellent"
    assert issues == []
    assert words > 60


def test_empty_description_scores_low():
    score, issues, suggestions, words, _ = score_content("Compression Leggings", "")
    assert score < 40
    assert words == 0
    assert "Description is empty" in issues
    assert suggestions


def test_short_description_is_flagged():
    score, issues, _, _, _ = score_content("Yoga Mat", "Good product for yoga.")
    assert score < 40
    assert any("short" in issue for issue in issues)


def test_filler_marketing_is_penalised():
    base = (
        "This mat is 6 mm thick closed-cell foam with a textured grip surface. "
        "Ideal for daily practice at home. It measures 1830 by 610 mm and weighs "
        "1.2 kg so it rolls up small enough for a backpack. Wipe clean after use "
        "with a damp cloth and leave it flat to dry overnight before rolling it."
    )
    clean_score, *_ = score_content("Grip Yoga Mat 6 mm", base)
    filler_score, filler_issues, *_ = score_content(
        "Grip Yoga Mat 6 mm", base + " High quality product."
    )
    assert filler_score < clean_score
    assert any("filler" in issue for issue in filler_issues)


def test_placeholder_title_zeroes_the_title_component():
    score, issues, _, _, _ = score_content("TODO write title", GOOD[1])
    assert any("placeholder" in issue for issue in issues)
    assert score < score_content(*GOOD)[0]


def test_all_caps_title_is_flagged():
    _, issues, _, _, _ = score_content("SPEEDSTER RACE FLAT SHOE", GOOD[1])
    assert any("all caps" in issue for issue in issues)


@pytest.mark.parametrize(
    "score,expected",
    [(100, "excellent"), (80, "excellent"), (70, "good"), (45, "weak"), (0, "poor")],
)
def test_grade_boundaries(score, expected):
    assert grade_for(score) == expected


def test_score_is_deterministic():
    assert score_content(*GOOD) == score_content(*GOOD)
