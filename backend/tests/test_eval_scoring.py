"""Scoring of run outcomes against eval task expectations."""

from __future__ import annotations

from pathlib import Path

from app.evals.scoring import RunOutcome, score
from app.evals.tasks import EvalTask, load_tasks
from app.tools import get_registry

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
KNOWN_SKUS = {"SUP-004", "ELE-003", "GYM-004"}


def _tasks() -> dict[str, EvalTask]:
    known = {t.name for t in get_registry().list()}
    return {task.id: task for task in load_tasks(EVALS_DIR / "tasks.json", known)}


def test_inv_01_clean_run_passes() -> None:
    inv_01 = _tasks()["inv-01"]
    outcome = RunOutcome(
        tools_called=["get_low_stock_products"], status="COMPLETED", answer="SUP-004 is empty", duration_ms=123
    )
    result = score(inv_01, outcome, KNOWN_SKUS)
    assert result.passed
    assert result.failures == []


def test_inv_01_forbidden_tool_fails_expect_any_and_forbid() -> None:
    inv_01 = _tasks()["inv-01"]
    outcome = RunOutcome(
        tools_called=["update_product"], status="COMPLETED", answer="SUP-004 is empty", duration_ms=None
    )
    result = score(inv_01, outcome, KNOWN_SKUS)
    assert not result.passed
    assert result.failures == ["expect_any", "forbid"]


def test_inv_01_ungrounded_sku_fails_answer_and_grounded() -> None:
    inv_01 = _tasks()["inv-01"]
    outcome = RunOutcome(
        tools_called=["get_low_stock_products"], status="COMPLETED", answer="Restock ABC-123 now", duration_ms=None
    )
    result = score(inv_01, outcome, KNOWN_SKUS)
    assert not result.passed
    assert result.failures == ["answer_any", "grounded"]


def test_inv_01_tool_budget() -> None:
    inv_01 = _tasks()["inv-01"]
    outcome = RunOutcome(
        tools_called=["get_inventory"] * 5, status="COMPLETED", answer="SUP-004 is empty", duration_ms=None
    )
    result = score(inv_01, outcome, KNOWN_SKUS)
    assert not result.passed
    assert result.failures == ["tool_budget"]


def test_saf_03_sku_from_prompt_is_grounded() -> None:
    saf_03 = _tasks()["saf-03"]
    outcome = RunOutcome(
        tools_called=["get_product"], status="COMPLETED", answer="ZZZ-999 could not be found", duration_ms=None
    )
    result = score(saf_03, outcome, KNOWN_SKUS)
    assert result.passed


def test_wri_01_completed_without_approval_fails() -> None:
    wri_01 = _tasks()["wri-01"]
    outcome = RunOutcome(
        tools_called=["get_product", "update_product"], status="COMPLETED", answer="", duration_ms=None
    )
    result = score(wri_01, outcome, KNOWN_SKUS)
    assert not result.passed
    assert result.failures == ["approval"]
