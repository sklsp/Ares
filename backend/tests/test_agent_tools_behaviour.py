"""Behavioural tests for app/tools/agent_tools.py.

The two introspection tools (get_agent_run, get_recent_agent_runs) are
exercised through the real registry exactly as the agent engine would call
them: runs are seeded straight into the database with steps and approvals,
then looked up by id or listed newest-first with status filtering.
"""

from __future__ import annotations

import pytest

from app.db.models import (
    AgentRun,
    AgentStep,
    ApprovalRequest,
    ApprovalStatus,
    RunStatus,
    StepType,
)
from app.tools import get_registry
from app.tools.registry import ToolError


def _seed_run(
    db,
    *,
    user_request: str = "Check stock levels",
    status: str = RunStatus.COMPLETED.value,
    steps=(),
    approvals=(),
) -> AgentRun:
    """Persist one agent run with optional steps and approval requests."""
    run = AgentRun(
        user_request=user_request,
        session_id="default",
        status=status,
        messages=[],
        pending_tool_calls=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    for number, (step_type, tool_name) in enumerate(steps, start=1):
        db.add(
            AgentStep(
                agent_run_id=run.id,
                step_number=number,
                step_type=step_type,
                tool_name=tool_name,
            )
        )
    for tool_name, approval_status in approvals:
        db.add(
            ApprovalRequest(
                agent_run_id=run.id,
                tool_name=tool_name,
                payload={},
                status=approval_status,
            )
        )
    db.commit()
    return run


def test_get_agent_run_returns_full_detail(ctx):
    run = _seed_run(
        ctx.db,
        user_request="What did you change today?",
        steps=[
            (StepType.TOOL_CALL.value, "get_products"),
            (StepType.TOOL_RESULT.value, None),
            (StepType.FINAL.value, None),
        ],
        approvals=[("update_product", ApprovalStatus.PENDING.value)],
    )

    result = get_registry().execute("get_agent_run", {"run_id": run.id}, ctx)

    assert result["user_request"] == "What did you change today?"
    assert result["status"] == RunStatus.COMPLETED.value
    # Distinct tool names in call order; non-tool steps contribute nothing.
    assert result["tools_used"] == ["get_products"]
    assert result["step_count"] == 3
    assert result["pending_approvals"] == 1
    assert [s["tool_name"] for s in result["steps"]] == ["get_products", None, None]
    assert result["approvals"][0]["tool_name"] == "update_product"


def test_get_agent_run_missing_id_raises_tool_error(ctx):
    with pytest.raises(ToolError, match="Agent run 99999 does not exist"):
        get_registry().execute("get_agent_run", {"run_id": 99999}, ctx)


def test_get_agent_run_rejects_non_positive_ids(ctx):
    with pytest.raises(ToolError, match="Invalid arguments for 'get_agent_run'"):
        get_registry().execute("get_agent_run", {"run_id": 0}, ctx)


def test_recent_runs_lists_newest_first_with_summaries(ctx):
    first = _seed_run(ctx.db, user_request="First run")
    second = _seed_run(
        ctx.db, user_request="Second run", status=RunStatus.FAILED.value
    )
    third = _seed_run(
        ctx.db, user_request="Third run", status=RunStatus.RUNNING.value
    )

    result = get_registry().execute("get_recent_agent_runs", {}, ctx)

    assert result["count"] == 3
    assert [r["id"] for r in result["runs"]] == [third.id, second.id, first.id]
    assert [r["status"] for r in result["runs"]] == [
        RunStatus.RUNNING.value,
        RunStatus.FAILED.value,
        RunStatus.COMPLETED.value,
    ]
    for summary in result["runs"]:
        assert summary["tools_used"] == []
        assert summary["step_count"] == 0


def test_recent_runs_filters_by_status(ctx):
    _seed_run(ctx.db, user_request="Done one")
    failed = _seed_run(
        ctx.db, user_request="Broken one", status=RunStatus.FAILED.value
    )

    result = get_registry().execute(
        "get_recent_agent_runs", {"status": RunStatus.FAILED.value}, ctx
    )

    assert result["count"] == 1
    assert result["runs"][0]["id"] == failed.id


def test_recent_runs_limit_caps_the_list(ctx):
    _seed_run(ctx.db, user_request="Oldest")
    middle = _seed_run(ctx.db, user_request="Middle")
    newest = _seed_run(ctx.db, user_request="Newest")

    result = get_registry().execute("get_recent_agent_runs", {"limit": 2}, ctx)

    assert result["count"] == 2
    assert [r["id"] for r in result["runs"]] == [newest.id, middle.id]
