"""Suite reports render to JSON and Markdown."""

from __future__ import annotations

import json

from app.evals.report import write_suite_report
from app.evals.runner import SuiteReport
from app.evals.scoring import TaskResult


def _report() -> SuiteReport:
    results = [
        TaskResult(
            task_id="inv-01",
            category="inventory",
            passed=True,
            checks={},
            failures=[],
            tools_called=["get_low_stock_products"],
        ),
        TaskResult(
            task_id="wri-02",
            category="safety",
            passed=False,
            checks={"forbid": False},
            failures=["forbid"],
            tools_called=["update_product"],
        ),
    ]
    return SuiteReport(
        name="demo",
        provider="oracle",
        model="task-script",
        results=results,
        by_category={"inventory": (1, 1), "safety": (0, 1)},
        passed=1,
        total=2,
        pass_rate=0.5,
    )


def test_write_suite_report(tmp_path) -> None:
    json_path, md_path = write_suite_report(_report(), tmp_path, {"tasks": "30"})
    assert json_path.name == "demo.json"
    assert md_path.name == "demo.md"

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["name"] == "demo"
    assert data["provider"] == "oracle"
    assert data["model"] == "task-script"
    assert data["tasks"] == "30"  # meta merged in
    assert (data["passed"], data["total"]) == (1, 2)
    assert [result["task_id"] for result in data["results"]] == ["inv-01", "wri-02"]

    expected = """\
# Agent eval: demo

| setting | value |
|---|---|
| provider | oracle |
| model | task-script |
| tasks | 30 |

| category | passed |
|---|---|
| inventory | 1/1 |
| safety | 0/1 |
| **total** | **1/2 (50.0%)** |

## Tasks

| task | category | result | failed checks | tools called |
|---|---|---|---|---|
| inv-01 | inventory | pass | - | get_low_stock_products |
| wri-02 | safety | FAIL | forbid | update_product |
"""
    text = md_path.read_text(encoding="utf-8")
    assert text == expected
