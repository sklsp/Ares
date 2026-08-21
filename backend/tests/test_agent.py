"""Agent loop: tool selection, multi-step runs, limits and failure handling."""

from __future__ import annotations

from app.agent import runner
from app.db.models import AgentRun, RunStatus, StepStatus, StepType
from tests.fakes import answer, tool_call, tool_calls


def run_agent(db, message: str) -> AgentRun:
    run = runner.create_run(db, message)
    runner.execute_run(run.id)
    db.expire_all()
    return db.get(AgentRun, run.id)


def step_messages(run: AgentRun) -> list[str]:
    return [s.message for s in run.steps]


def tool_steps(run: AgentRun, step_type: StepType) -> list:
    return [s for s in run.steps if s.step_type == step_type.value]


# --- tool selection ---------------------------------------------------
def test_agent_calls_the_tool_it_chose_and_answers(db, llm):
    llm.script(
        tool_call("get_low_stock_products", limit=5),
        answer("Two products are below their reorder point: Compression Leggings and Yoga Mat."),
    )
    run = run_agent(db, "Which products have the lowest inventory?")

    assert run.status == RunStatus.COMPLETED.value
    assert "reorder point" in run.final_response
    calls = tool_steps(run, StepType.TOOL_CALL)
    assert [s.tool_name for s in calls] == ["get_low_stock_products"]
    assert run.duration_ms is not None


def test_the_model_is_offered_every_tool(db, llm, registry):
    llm.script(answer("Nothing to do."))
    run_agent(db, "hello")
    assert llm.offered_tool_names() == {t.name for t in registry.list()}


def test_tool_results_are_fed_back_into_the_conversation(db, llm):
    llm.script(tool_call("get_products", limit=5), answer("You have four products."))
    run_agent(db, "How many products are there?")

    results = llm.last_tool_results()
    assert len(results) == 1
    assert "RUN-001" in results[0].content
    assert results[0].name == "get_products"


def test_agent_chains_several_tools_in_one_run(db, llm):
    llm.script(
        tool_call("get_products", limit=25),
        tool_call("analyze_product_content", limit=3),
        tool_call("get_product", sku="CLO-004"),
        answer("The three weakest products are CLO-004, GYM-003 and RUN-003."),
    )
    run = run_agent(db, "Find the products with the worst descriptions")

    assert run.status == RunStatus.COMPLETED.value
    assert [s.tool_name for s in tool_steps(run, StepType.TOOL_CALL)] == [
        "get_products",
        "analyze_product_content",
        "get_product",
    ]
    assert run.tool_calls_made == 3


def test_parallel_tool_calls_in_one_decision_all_execute(db, llm):
    llm.script(
        tool_calls(
            ("get_products", {"limit": 5}),
            ("get_sales_summary", {"days": 30}),
        ),
        answer("Combined report."),
    )
    run = run_agent(db, "Give me a catalog and sales overview")

    assert [s.tool_name for s in tool_steps(run, StepType.TOOL_CALL)] == [
        "get_products",
        "get_sales_summary",
    ]
    assert run.status == RunStatus.COMPLETED.value


# --- observability ----------------------------------------------------
def test_run_records_an_operational_step_trail(db, llm):
    llm.script(tool_call("get_products", limit=5), answer("Four products."))
    run = run_agent(db, "list products")

    types = [s.step_type for s in run.steps]
    assert types == [
        StepType.REQUEST.value,
        StepType.DECISION.value,
        StepType.TOOL_CALL.value,
        StepType.TOOL_RESULT.value,
        StepType.FINAL.value,
    ]
    assert [s.step_number for s in run.steps] == [1, 2, 3, 4, 5]
    assert "Retrieved 4 products" in step_messages(run)[3]


def test_tool_input_and_output_are_persisted(db, llm):
    llm.script(tool_call("get_product", sku="GYM-003"), answer("Found it."))
    run = run_agent(db, "show me the yoga mat")

    call_step = tool_steps(run, StepType.TOOL_CALL)[0]
    result_step = tool_steps(run, StepType.TOOL_RESULT)[0]
    assert call_step.input == {"sku": "GYM-003"}
    assert result_step.output["sku"] == "GYM-003"


# --- failure handling -------------------------------------------------
def test_unknown_tool_does_not_kill_the_run(db, llm):
    llm.script(tool_call("delete_the_database"), answer("I could not do that."))
    run = run_agent(db, "delete everything")

    assert run.status == RunStatus.COMPLETED.value
    errors = [s for s in run.steps if s.status == StepStatus.ERROR.value]
    assert errors and "Unknown tool" in errors[0].message


def test_invalid_tool_arguments_are_reported_back_to_the_model(db, llm):
    llm.script(tool_call("get_products", limit=9999), answer("Adjusted and retried."))
    run = run_agent(db, "list a million products")

    result_step = tool_steps(run, StepType.TOOL_RESULT)[0]
    assert result_step.status == StepStatus.ERROR.value
    assert "Invalid arguments" in result_step.output["error"]
    assert run.status == RunStatus.COMPLETED.value


def test_failing_tool_is_returned_as_data_not_an_exception(db, llm):
    llm.script(tool_call("get_product", product_id=98765), answer("That product does not exist."))
    run = run_agent(db, "show product 98765")

    result_step = tool_steps(run, StepType.TOOL_RESULT)[0]
    assert "not found" in result_step.output["error"]
    assert run.status == RunStatus.COMPLETED.value


def test_unreachable_llm_fails_the_run_cleanly(db, llm):
    llm.available = False
    run = run_agent(db, "anything")

    assert run.status == RunStatus.FAILED.value
    assert "Cannot reach" in run.error
    assert run.final_response
    assert run.steps[-1].step_type == StepType.ERROR.value


# --- limits -----------------------------------------------------------
def test_iteration_limit_stops_the_loop(db, llm):
    # AGENT_MAX_ITERATIONS is 5 in the test environment.
    llm.fallback = tool_call("get_products", limit=5)
    run = run_agent(db, "loop forever")

    assert run.status == RunStatus.COMPLETED.value
    assert run.iterations == 5
    assert "Stopped early" in run.final_response
    assert any("Stopping early" in m for m in step_messages(run))


def test_tool_call_limit_stops_the_loop(db, llm):
    # AGENT_MAX_TOOL_CALLS is 8; three calls per decision hits it before the
    # iteration limit does.
    llm.fallback = tool_calls(
        ("get_products", {"limit": 5}),
        ("get_inventory", {"limit": 5}),
        ("get_sales_summary", {"days": 30}),
    )
    run = run_agent(db, "keep calling tools")

    assert run.tool_calls_made >= 8
    assert run.iterations < 5
    assert run.status == RunStatus.COMPLETED.value


def test_run_without_tool_calls_answers_directly(db, llm):
    llm.script(answer("You have not given me anything to look up."))
    run = run_agent(db, "hi")

    assert run.tool_calls_made == 0
    assert run.status == RunStatus.COMPLETED.value
    assert len(run.steps) == 2  # request + final
