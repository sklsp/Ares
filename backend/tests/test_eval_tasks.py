"""Loading and validation of evals/tasks.json."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from app.evals.tasks import EvalTask, load_tasks
from app.tools import get_registry

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
KNOWN_TOOLS = {"get_low_stock_products", "update_product"}


def _task(**overrides: Any) -> dict[str, Any]:
    """A complete valid task; override individual fields per test."""
    base: dict[str, Any] = {
        "id": "t-01",
        "category": "inventory",
        "prompt": "Which products are below their reorder point?",
        "expect_any": ["get_low_stock_products"],
        "expect_all": [],
        "forbid": ["update_product"],
        "expect_approval": False,
        "answer_any": ["SUP-004"],
        "answer_none": [],
        "max_tool_calls": 4,
        "oracle": [{"tool": "get_low_stock_products", "args": {}}],
        "oracle_answer": "Seven products are at or below their reorder point.",
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, tasks: list[dict[str, Any]]) -> Path:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return path


def test_real_task_file_loads_30_tasks() -> None:
    known = {t.name for t in get_registry().list()}
    tasks = load_tasks(EVALS_DIR / "tasks.json", known)
    assert len(tasks) == 30
    assert all(isinstance(t, EvalTask) for t in tasks)


def test_real_task_file_category_counts() -> None:
    known = {t.name for t in get_registry().list()}
    tasks = load_tasks(EVALS_DIR / "tasks.json", known)
    counts = Counter(t.category for t in tasks)
    assert dict(counts) == {"inventory": 6, "sales": 6, "content": 6, "catalog": 5, "approval": 4, "safety": 3}


def test_missing_field_raises(tmp_path: Path) -> None:
    task = _task()
    del task["category"]
    with pytest.raises(ValueError) as exc_info:
        load_tasks(_write(tmp_path, [task]), KNOWN_TOOLS)
    assert str(exc_info.value) == "t-01: missing field 'category'"


def test_duplicate_id_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_tasks(_write(tmp_path, [_task(), _task()]), KNOWN_TOOLS)
    assert str(exc_info.value) == "duplicate task id 't-01'"


def test_unknown_tool_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_tasks(_write(tmp_path, [_task(expect_any=["no_such_tool"])]), KNOWN_TOOLS)
    assert str(exc_info.value) == "t-01: unknown tool 'no_such_tool'"


def test_approval_task_without_update_product_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        load_tasks(_write(tmp_path, [_task(expect_approval=True)]), KNOWN_TOOLS)
    assert str(exc_info.value) == "t-01: an approval task needs update_product in expect_all"
