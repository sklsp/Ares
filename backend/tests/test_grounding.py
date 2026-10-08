"""Regression tests for the fabrication-after-tool-failure bug.

Production observation (llama3.2, live):
    model requested get_products(limit=500) -> schema rejected it ->
    model received the validation error -> model answered
    "There are 987 products" (fabricated).

The execution layer must guarantee: a failed tool call leads to
retry -> verified result, or safe failure -> honest explanation.
Never failed tool call -> invented answer.
"""

from __future__ import annotations

from app.agent import runner
from app.agent.grounding import GroundingPolicy, classify_response
from app.db.models import AgentRun, RunStatus, StepStatus, StepType
from tests.fakes import answer, tool_call


def run_agent(db, message: str) -> AgentRun:
    run = runner.create_run(db, message)
    runner.execute_run(run.id)
    db.expire_all()
    return db.get(AgentRun, run.id)


def tool_results(run: AgentRun) -> list:
    return [s for s in run.steps if s.step_type == StepType.TOOL_RESULT.value]


# --- reproduction -------------------------------------------------------
def test_invalid_argument_then_retry_succeeds_and_grounded_answer(db, llm):
    """The exact production scenario, with a well-behaved recovery."""
    llm.script(
        tool_call("get_products", limit=500),      # schema rejects (max 100)
        tool_call("get_products", limit=5),        # corrected retry
        answer("The catalog contains 4 products in total."),
    )
    run = run_agent(db, "How many products do I have?")

    assert run.status == RunStatus.COMPLETED.value
    results = tool_results(run)
    # First call failed validation; second succeeded.
    assert results[0].status == StepStatus.ERROR.value
    assert results[1].status == StepStatus.OK.value
    # The final answer is grounded in the successful call's actual data.
    assert "4" in run.final_response


def test_invalid_arguments_exhausted_produces_safe_failure(db, llm):
    """Repeated invalid arguments must end in an honest failure, never data."""
    llm.script(
        tool_call("get_products", limit=500),
        tool_call("get_products", limit=999),
        tool_call("get_products", limit=1000),
        # The model then tries to answer anyway. This MUST be blocked.
        answer("You have 987 products in your catalog."),
    )
    run = run_agent(db, "How many products do I have?")

    # The fabricated answer must not be presented as the run result.
    assert run.final_response is None or "987" not in run.final_response
    assert run.status == RunStatus.FAILED.value or (
        run.final_response and "could not" in run.final_response.lower()
    )


def test_validation_error_message_is_actionable(db, llm):
    """The model receives a machine-readable correction it can act on."""
    llm.script(
        tool_call("get_products", limit=500),
        tool_call("get_products", limit=5),
        answer("Done."),
    )
    run = run_agent(db, "list products")

    first_error = tool_results(run)[0].output
    assert first_error["category"] == "VALIDATION_ERROR"
    assert first_error["retryable"] is True
    # The correction names the field, the allowed bound, and asks for a retry.
    correction = first_error["correction"]
    assert "VALIDATION_ERROR" in correction
    assert "limit" in correction
    assert "<= 100" in correction
    assert "Retry the tool" in correction
