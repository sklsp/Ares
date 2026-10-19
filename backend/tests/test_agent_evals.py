"""Agent behaviour evaluations (portfolio items 63-71).

Scripted end-to-end scenarios: a deterministic FakeLLM drives the real agent
engine against the fully seeded demo store, and each test asserts on
observable behaviour - steps, tool results, run status, approval state and
database side effects. No model server is involved; if any of these fail,
agent behaviour has regressed.

Scenarios:
  63  multi-step chain: content analysis -> product detail lookup
  64  sales summary + best sellers in one run
  65  invalid tool arguments are rejected with a correction and retried
  67  write tools pause for human approval; approved changes land in the DB
  68  rejected writes never execute
  69  fabricated numbers after a tool failure are blocked by the grounding gate
  70  iteration limit: the run stops cleanly and still answers
  71  full loop: find weakest copy, draft a better description, apply with approval
"""

from __future__ import annotations

from app.agent import runner
from app.db.models import (
    AgentRun,
    ApprovalRequest,
    ApprovalStatus,
    Product,
    RunStatus,
    StepStatus,
    StepType,
)
from app.integrations.base import ProviderError
from app.integrations.mock_provider import MockEcommerceProvider
from app.seed import seed
from app.services import approvals as approval_service
from app.services.content_quality import analyze_product
from tests.fakes import FakeLLM, answer, tool_call


# --- helpers ------------------------------------------------------------
def _run(db, llm: FakeLLM, message: str) -> AgentRun:
    run = runner.create_run(db, message)
    runner.execute_run(run.id)
    db.expire_all()
    return db.get(AgentRun, run.id)


def _steps(run: AgentRun, step_type: StepType):
    return [s for s in run.steps if s.step_type == step_type.value]


def _tool_results(run: AgentRun):
    return _steps(run, StepType.TOOL_RESULT)


def _seeded(db) -> None:
    """Replace the 4-product test catalog with the full 42-product demo store."""
    seed(db)
    db.expire_all()


def _weakest_copy(db):
    """The product the analyze_product_content tool would rank first (weakest)."""
    details, _ = MockEcommerceProvider(db).get_products(limit=100)
    analyses = [analyze_product(d) for d in details]
    return min(analyses, key=lambda a: (a.score, a.product_id))


def _pending_approval(run: AgentRun) -> ApprovalRequest:
    return next(a for a in run.approvals if a.status == ApprovalStatus.PENDING.value)


# --- 63: multi-step chain -------------------------------------------------
def test_eval_63_chains_analysis_into_product_lookup(db, llm):
    _seeded(db)
    weakest = _weakest_copy(db)
    llm.script(
        tool_call("analyze_product_content", limit=5),
        tool_call("get_product", product_id=weakest.product_id),
        answer(f"The weakest copy in the catalog is {weakest.sku} ({weakest.title}), "
               f"scoring {weakest.score}/100."),
    )
    run = _run(db, llm, "Which product has the worst description?")

    assert run.status == RunStatus.COMPLETED.value
    calls = [s.tool_name for s in _steps(run, StepType.TOOL_CALL)]
    assert calls == ["analyze_product_content", "get_product"]
    results = _tool_results(run)
    assert all(r.status == StepStatus.OK.value for r in results)
    # The follow-up lookup must target the product the analysis ranked weakest.
    assert results[0].output["products"][0]["sku"] == weakest.sku
    call_steps = _steps(run, StepType.TOOL_CALL)
    assert call_steps[1].input["product_id"] == weakest.product_id
    assert weakest.sku in run.final_response


# --- 64: sales reporting ---------------------------------------------------
def test_eval_64_sales_summary_and_best_sellers(db, llm):
    _seeded(db)
    llm.script(
        tool_call("get_sales_summary", days=30),
        tool_call("get_best_sellers", days=30, limit=5),
        answer("I pulled the 30-day sales summary and the top five sellers; "
               "the per-category breakdown is in the results above."),
    )
    run = _run(db, llm, "How did the store sell over the last month?")

    assert run.status == RunStatus.COMPLETED.value
    calls = [s.tool_name for s in _steps(run, StepType.TOOL_CALL)]
    assert calls == ["get_sales_summary", "get_best_sellers"]
    summary, sellers = _tool_results(run)
    assert summary.output["period_days"] == 30
    assert summary.output["total_revenue"] > 0
    assert len(summary.output["by_category"]) >= 1
    assert sellers.output["count"] == 5
    assert all(p["units_sold"] > 0 for p in sellers.output["products"])


# --- 65: invalid arguments are corrected and retried -----------------------
def test_eval_65_invalid_arguments_are_corrected_and_retried(db, llm):
    _seeded(db)
    llm.script(
        tool_call("get_low_stock_products", limit=250),  # exceeds the documented max of 100
        tool_call("get_low_stock_products", limit=20),
        answer("I checked stock levels with a valid query and can report which "
               "products are below their reorder point."),
    )
    run = _run(db, llm, "Which products are running low on stock?")

    assert run.status == RunStatus.COMPLETED.value
    bad, good = _tool_results(run)
    assert bad.status == StepStatus.ERROR.value
    assert bad.output["category"] == "VALIDATION_ERROR"
    assert bad.output["retryable"] is True
    assert "limit" in bad.output["correction"]
    assert good.status == StepStatus.OK.value



# --- 67: write tool waits for approval, then applies -----------------------
def test_eval_67_write_tool_waits_for_approval_then_applies(db, llm):
    _seeded(db)
    target = db.query(Product).filter(Product.sku == "RUN-003").one()
    original = target.description or ""
    new_description = (
        "The Trail Runner X is a lightweight daily trainer with a breathable knit upper and a "
        "cushioned EVA midsole that absorbs impact on pavement. Ideal for beginners building up "
        "to longer runs, it weighs 260 g and dries quickly in the wash."
    )
    llm.script(
        tool_call("update_product", product_id=target.id, description=new_description),
        answer("The new description has been applied to RUN-003."),
    )
    run = runner.create_run(db, "Rewrite the description of product RUN-003.")
    runner.execute_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)

    # Paused: nothing executed yet.
    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    approval = _pending_approval(run)
    assert approval.tool_name == "update_product"
    assert approval.payload["product_id"] == target.id
    db.refresh(target)
    assert target.description == original  # unchanged while pending

    approval_service.resolve(db, approval, approved=True, note="looks good")
    runner.resume_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)
    db.refresh(target)

    assert run.status == RunStatus.COMPLETED.value
    assert target.description == new_description  # the change landed in the DB
    approval = db.get(ApprovalRequest, approval.id)
    assert approval.status == ApprovalStatus.APPROVED.value
    assert approval.result is not None


# --- 68: rejected writes never execute --------------------------------------
def test_eval_68_rejected_write_never_executes(db, llm):
    _seeded(db)
    target = db.query(Product).filter(Product.sku == "RUN-003").one()
    original = target.description or ""
    new_description = "A completely different description that must not be applied."
    llm.script(
        tool_call("update_product", product_id=target.id, description=new_description),
        answer("The change was rejected, so RUN-003's description is unchanged."),
    )
    run = runner.create_run(db, "Rewrite the description of product RUN-003.")
    runner.execute_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)

    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    approval = _pending_approval(run)
    approval_service.resolve(db, approval, approved=False, note="not now")
    runner.resume_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)
    db.refresh(target)

    assert run.status == RunStatus.COMPLETED.value
    assert target.description == original  # the write never happened
    approval = db.get(ApprovalRequest, approval.id)
    assert approval.status == ApprovalStatus.REJECTED.value
    update_result = next(
        s for s in _tool_results(run) if s.tool_name == "update_product"
    )
    assert update_result.status == StepStatus.ERROR.value
    assert update_result.output.get("executed") is False



# --- 69: fabricated numbers after a failure are blocked ---------------------
def test_eval_69_fabricated_numbers_after_failure_are_blocked(db, llm, monkeypatch):
    _seeded(db)

    def broken_get_products(self, **kwargs):
        raise ProviderError("catalog service unavailable")

    monkeypatch.setattr(MockEcommerceProvider, "get_products", broken_get_products)
    llm.script(
        tool_call("get_products", limit=50),
        answer("You have 42 products in the catalog."),  # fabricated -> must be blocked
        answer("I could not verify the product count because the catalog lookup failed."),
    )
    run = _run(db, llm, "How many products do we have?")

    assert run.status == RunStatus.COMPLETED.value
    assert "42" not in (run.final_response or "")  # the invented number never shipped
    results = _tool_results(run)
    assert results[0].status == StepStatus.ERROR.value
    # Provider errors are wrapped at the registry boundary; either classification
    # is acceptable, but it must be a classified (retryable) error.
    assert results[0].output["category"] in ("UPSTREAM_ERROR", "INTERNAL_ERROR")
    assert llm.call_count == 3  # failed call, blocked answer, honest retry


# --- 70: iteration limit stops cleanly --------------------------------------
def test_eval_70_iteration_limit_stops_cleanly(db, llm):
    _seeded(db)
    ids = [p.id for p in db.query(Product).order_by(Product.id).limit(5)]
    llm.script(
        *(tool_call("get_product", product_id=pid) for pid in ids),  # 5 calls = the limit
        answer("I stopped early because I hit the iteration limit after checking five products."),
    )
    run = _run(db, llm, "Check these five products one by one and tell me about each.")

    assert run.status == RunStatus.COMPLETED.value
    assert run.iterations == 5
    assert len(_steps(run, StepType.TOOL_CALL)) == 5
    # The wrap-up answer was generated without any tools offered.
    decision = _steps(run, StepType.DECISION)
    assert any("Stopping early" in s.message for s in decision)
    assert llm.offered_tool_names() == set()
    assert "iteration limit" in run.final_response


# --- 71: find weakest copy, draft a better one, apply with approval ----------
def _draft_for(title: str, category: str) -> str:
    """A concrete, specific description that should outscore any weak copy."""
    return (
        f"{title} is a practical {category.lower()} choice for people who want "
        "reliable everyday performance without paying for features they will never use. The design "
        "keeps things simple and durable: the main body uses materials that hold up with regular "
        "use, it weighs about 250 g so it stays easy to carry or store, and setup takes under two "
        "minutes with no tools needed. Ideal for daily use at home, in the gym or on short trips "
        "where you need something that just works. Care is straightforward: wipe it clean with a "
        "damp cloth and let it air dry before storing."
    )


def test_eval_71_find_weakest_copy_draft_and_apply_with_approval(db, llm):
    _seeded(db)
    weakest = _weakest_copy(db)
    target = db.get(Product, weakest.product_id)
    original = target.description or ""
    draft = _draft_for(weakest.title, target.category)

    llm.script(
        tool_call("analyze_product_content", limit=5),
        tool_call("generate_product_description", product_id=weakest.product_id),
        answer(draft),  # consumed by the drafting tool's internal LLM call
        tool_call("update_product", product_id=weakest.product_id, description=draft),
        answer(f"The new description for {weakest.sku} has been applied."),
    )
    run = runner.create_run(db, "Improve the worst product description in our catalog.")
    runner.execute_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)

    # Paused at the write; the draft itself already ran and must have improved quality.
    assert run.status == RunStatus.WAITING_FOR_APPROVAL.value
    generate_result = next(
        s for s in _tool_results(run) if s.tool_name == "generate_product_description"
    )
    assert generate_result.output["proposed_value"] == draft
    assert generate_result.output["proposed_score"] > generate_result.output["current_score"]
    db.refresh(target)
    assert target.description == original  # nothing applied before approval

    approval = _pending_approval(run)
    assert approval.tool_name == "update_product"
    approval_service.resolve(db, approval, approved=True, note="ship it")
    runner.resume_run(run.id)
    db.expire_all()
    run = db.get(AgentRun, run.id)
    db.refresh(target)

    assert run.status == RunStatus.COMPLETED.value
    assert target.description == draft  # the improved copy is live in the DB
    approval = db.get(ApprovalRequest, approval.id)
    assert approval.status == ApprovalStatus.APPROVED.value
    assert weakest.sku in (run.final_response or "")

