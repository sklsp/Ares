"""Human-in-the-loop: writes must not happen without an explicit approval."""

from __future__ import annotations

import pytest

from app.agent import runner
from app.db.models import (
    AgentRun,
    ApprovalRequest,
    ApprovalStatus,
    Product,
    RunStatus,
    StepType,
)
from app.services import approvals as approval_service
from tests.fakes import answer, tool_call, tool_calls

NEW_COPY = (
    "The Trailhead 2 is a cushioned road shoe for runners covering 30 to 50 km a week. "
    "Its 28 mm EVA midsole and 250 g weight keep long efforts comfortable, and the mesh "
    "upper dries fast after wet runs. Ideal for daily training and weekend long runs."
)


def start(db, message: str) -> AgentRun:
    run = runner.create_run(db, message)
    runner.execute_run(run.id)
    db.expire_all()
    return db.get(AgentRun, run.id)


def pending_approval(db, run: AgentRun) -> ApprovalRequest:
    return next(a for a in run.approvals if a.status == ApprovalStatus.PENDING.value)


@pytest.fixture
def paused_run(db, llm, product_id):
    """A run stopped at an update_product approval."""
    llm.script(
        tool_call("analyze_product_content", limit=1),
        tool_call("update_product", product_id=product_id, description=NEW_COPY),
        answer("I updated the product description."),
    )
    return start(db, "Improve the description of the worst product")


# --- the pause --------------------------------------------------------
def test_write_pauses_the_run_and_changes_nothing(db, paused_run, product_id):
    assert paused_run.status == RunStatus.WAITING_FOR_APPROVAL.value
    assert paused_run.completed_at is None

    approval = pending_approval(db, paused_run)
    assert approval.tool_name == "update_product"
    assert approval.payload["description"] == NEW_COPY

    # Nothing was written.
    assert db.get(Product, product_id).description == "Comfortable running shoes."
    assert not any(s.tool_name == "update_product" for s in paused_run.steps
                   if s.step_type == StepType.TOOL_RESULT.value)


def test_approval_carries_a_before_after_preview(db, paused_run, product_id):
    preview = pending_approval(db, paused_run).preview
    change = preview["changes"][0]

    assert change["field"] == "description"
    assert change["current"] == "Comfortable running shoes."
    assert change["proposed"] == NEW_COPY
    assert preview["content_score"]["proposed"] > preview["content_score"]["current"]


def test_pause_is_visible_in_the_step_trail(db, paused_run):
    steps = [s for s in paused_run.steps if s.step_type == StepType.APPROVAL_REQUEST.value]
    assert len(steps) == 1
    assert "Waiting for approval" in steps[0].message
    assert steps[0].status == "pending"


def test_read_tools_still_ran_before_the_pause(db, paused_run):
    executed = [
        s.tool_name for s in paused_run.steps if s.step_type == StepType.TOOL_RESULT.value
    ]
    assert executed == ["analyze_product_content"]


# --- approve ----------------------------------------------------------
def test_approving_executes_the_write(db, paused_run, product_id):
    approval = pending_approval(db, paused_run)
    approval_service.resolve(db, approval, approved=True)
    runner.resume_run(paused_run.id)
    db.expire_all()

    run = db.get(AgentRun, paused_run.id)
    assert run.status == RunStatus.COMPLETED.value
    assert db.get(Product, product_id).description == NEW_COPY

    resolved = db.get(ApprovalRequest, approval.id)
    assert resolved.status == ApprovalStatus.APPROVED.value
    assert resolved.resolved_at is not None
    assert resolved.result["updated_fields"] == ["description"]


def test_approved_run_records_the_execution(db, paused_run):
    approval_service.resolve(db, pending_approval(db, paused_run), approved=True)
    runner.resume_run(paused_run.id)
    db.expire_all()

    run = db.get(AgentRun, paused_run.id)
    messages = [s.message for s in run.steps]
    assert any("Approval granted" in m for m in messages)
    assert any(m.startswith("Updated RUN-003") for m in messages)
    assert run.final_response


# --- reject -----------------------------------------------------------
def test_rejecting_leaves_the_database_untouched(db, paused_run, product_id):
    approval = pending_approval(db, paused_run)
    approval_service.resolve(db, approval, approved=False, note="Tone is wrong")
    runner.resume_run(paused_run.id)
    db.expire_all()

    run = db.get(AgentRun, paused_run.id)
    assert run.status == RunStatus.COMPLETED.value
    assert db.get(Product, product_id).description == "Comfortable running shoes."

    resolved = db.get(ApprovalRequest, approval.id)
    assert resolved.status == ApprovalStatus.REJECTED.value
    assert resolved.decision_note == "Tone is wrong"
    assert resolved.result is None


def test_rejection_is_recorded_and_reported_to_the_model(db, paused_run, llm):
    approval_service.resolve(db, pending_approval(db, paused_run), approved=False)
    runner.resume_run(paused_run.id)
    db.expire_all()

    run = db.get(AgentRun, paused_run.id)
    assert any("rejected" in s.message for s in run.steps)

    rejection = next(m for m in llm.last_tool_results() if m.name == "update_product")
    assert "rejected" in rejection.content
    assert '"executed": false' in rejection.content


# --- guards -----------------------------------------------------------
def test_an_approval_cannot_be_resolved_twice(db, paused_run):
    approval = pending_approval(db, paused_run)
    approval_service.resolve(db, approval, approved=True)
    with pytest.raises(approval_service.AlreadyResolvedError):
        approval_service.resolve(db, approval, approved=False)


def test_resuming_before_a_decision_keeps_the_run_paused(db, paused_run, product_id):
    runner.resume_run(paused_run.id)
    db.expire_all()

    run = db.get(AgentRun, paused_run.id)
    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    assert db.get(Product, product_id).description == "Comfortable running shoes."


def test_each_write_in_a_batch_gets_its_own_approval(db, llm, product_id):
    other_id = db.query(Product).filter(Product.sku == "GYM-003").one().id
    llm.script(
        tool_calls(
            ("update_product", {"product_id": product_id, "description": NEW_COPY}),
            ("update_product", {"product_id": other_id, "title": "Grip Yoga Mat 6 mm"}),
        ),
        answer("Both products were updated."),
    )
    run = start(db, "Improve both products")

    # First write pauses; the second has not been proposed yet.
    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    assert len(run.approvals) == 1

    approval_service.resolve(db, pending_approval(db, run), approved=True)
    runner.resume_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)

    # Second write now pauses in turn.
    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    assert len(run.approvals) == 2
    assert db.get(Product, product_id).description == NEW_COPY
    assert db.get(Product, other_id).title == "Yoga Mat"

    approval_service.resolve(db, pending_approval(db, run), approved=True)
    runner.resume_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)

    assert run.status == RunStatus.COMPLETED.value
    assert db.get(Product, other_id).title == "Grip Yoga Mat 6 mm"


def test_mixed_batch_runs_reads_immediately_and_holds_the_write(db, llm, product_id):
    llm.script(
        tool_calls(
            ("get_low_stock_products", {"limit": 5}),
            ("update_product", {"product_id": product_id, "description": NEW_COPY}),
        ),
        answer("Done."),
    )
    run = start(db, "Check stock then fix the description")

    executed = [
        s.tool_name for s in run.steps if s.step_type == StepType.TOOL_RESULT.value
    ]
    assert executed == ["get_low_stock_products"]
    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    assert db.get(Product, product_id).description == "Comfortable running shoes."
