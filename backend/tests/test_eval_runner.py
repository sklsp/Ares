"""Running one eval task through the real agent engine."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import Product
from app.evals.providers import LazyProvider, OracleProvider
from app.evals.runner import (
    SuiteReport,
    known_skus,
    offline_tools,
    run_suite,
    run_task,
    seed_demo,
    sku_resolver,
)
from app.evals.tasks import EvalTask, load_tasks
from app.integrations.mock_provider import MockEcommerceProvider
from app.tools import ToolContext, get_registry
from app.tools.registry import ToolError

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"


def _tasks() -> dict[str, EvalTask]:
    known = {t.name for t in get_registry().list()}
    return {task.id: task for task in load_tasks(EVALS_DIR / "tasks.json", known)}


def test_inv_01_completes(db: Session) -> None:
    seed_demo(db)
    task = _tasks()["inv-01"]
    outcome = run_task(db, task, OracleProvider(task, sku_resolver(db)))
    assert outcome.status == "COMPLETED"
    assert outcome.tools_called == ["get_low_stock_products"]


def test_wri_01_waits_for_approval(db: Session) -> None:
    seed_demo(db)
    task = _tasks()["wri-01"]
    outcome = run_task(db, task, OracleProvider(task, sku_resolver(db)))
    assert outcome.status == "WAITING_FOR_APPROVAL"
    assert outcome.tools_called == ["get_product", "update_product"]


def test_sku_resolver_maps_and_rejects(db: Session) -> None:
    seed_demo(db)
    resolve = sku_resolver(db)
    product_id = db.query(Product).filter_by(sku="SUP-004").one().id
    assert resolve("SUP-004") == product_id
    with pytest.raises(KeyError, match="unknown SKU ZZZ-999"):
        resolve("ZZZ-999")


def test_offline_tools_disables_and_restores(db: Session) -> None:
    registry = get_registry()
    original = registry.get("crawl_website").fn
    ctx = ToolContext(db=db, provider=MockEcommerceProvider(db), llm=LazyProvider())
    with offline_tools():
        with pytest.raises(ToolError, match="disabled during evals"):
            registry.get("crawl_website").fn(ctx, None)
    assert registry.get("crawl_website").fn is original


def test_known_skus_covers_demo_catalog(db: Session) -> None:
    seed_demo(db)
    assert {"SUP-004", "ELE-003", "GYM-004"} <= known_skus(db)


def _all_tasks() -> list[EvalTask]:
    known = {t.name for t in get_registry().list()}
    return load_tasks(EVALS_DIR / "tasks.json", known)


def test_oracle_suite_passes_everything(db: Session) -> None:
    report = run_suite(
        db,
        _all_tasks(),
        lambda task: OracleProvider(task, sku_resolver(db)),
        name="oracle",
    )
    assert isinstance(report, SuiteReport)
    assert (report.provider, report.model) == ("oracle", "task-script")
    assert report.total == 30
    assert report.passed == 30
    assert report.pass_rate == 1.0


def test_lazy_suite_fails_most(db: Session) -> None:
    report = run_suite(db, _all_tasks(), lambda task: LazyProvider(), name="lazy")
    assert (report.provider, report.model) == ("lazy", "no-tools")
    assert report.passed < 10
    inv_01 = next(result for result in report.results if result.task_id == "inv-01")
    assert inv_01.failures == ["expect_any", "answer_any"]

