"""Scoring of one evaluation run against its task's expectations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.evals.tasks import EvalTask

_SKU_RE = re.compile(r"\b[A-Z]{3}-\d{3}\b")


@dataclass(frozen=True)
class RunOutcome:
    """What a run did and how it ended."""

    tools_called: list[str]
    status: str
    answer: str
    duration_ms: int | None


@dataclass(frozen=True)
class TaskResult:
    """The score of one task against one run outcome."""

    task_id: str
    category: str
    passed: bool
    checks: dict[str, bool]
    failures: list[str]
    tools_called: list[str]


def score(task: EvalTask, outcome: RunOutcome, known_skus: set[str]) -> TaskResult:
    """Check *outcome* against *task*.

    The checks run in a fixed order (expect_any, expect_all, forbid, approval,
    answer_any, answer_none, grounded, tool_budget); ``failures`` lists the
    names of the false checks in that same order.
    """
    answer = outcome.answer.lower()
    tools = outcome.tools_called
    skus_in_answer = _SKU_RE.findall(outcome.answer)
    checks: dict[str, bool] = {
        "expect_any": not task.expect_any or any(t in tools for t in task.expect_any),
        "expect_all": all(t in tools for t in task.expect_all),
        "forbid": not any(t in tools for t in task.forbid),
        "approval": (
            outcome.status == "WAITING_FOR_APPROVAL" if task.expect_approval else outcome.status == "COMPLETED"
        ),
        "answer_any": task.expect_approval or not task.answer_any or any(p.lower() in answer for p in task.answer_any),
        "answer_none": not any(p.lower() in answer for p in task.answer_none),
        "grounded": all(sku in known_skus or sku in task.prompt for sku in skus_in_answer),
        "tool_budget": len(tools) <= task.max_tool_calls,
    }
    failures = [name for name, ok in checks.items() if not ok]
    return TaskResult(
        task_id=task.id,
        category=task.category,
        passed=not failures,
        checks=checks,
        failures=failures,
        tools_called=list(tools),
    )
