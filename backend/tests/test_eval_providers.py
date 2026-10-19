"""The oracle and lazy providers replay scripts deterministically."""

from __future__ import annotations

from pathlib import Path

from app.evals.providers import DRAFT, LazyProvider, OracleProvider
from app.evals.tasks import load_tasks
from app.tools import get_registry

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"


def _sal_06():
    known = {t.name for t in get_registry().list()}
    return next(t for t in load_tasks(EVALS_DIR / "tasks.json", known) if t.id == "sal-06")


def test_oracle_replays_script_in_order() -> None:
    provider = OracleProvider(_sal_06(), {"RUN-001": 1}.__getitem__)
    tools = [{"x": 1}]

    first = provider.chat([], tools)
    assert [call.name for call in first.tool_calls] == ["search_products"]
    assert first.tool_calls[0].arguments == {"query": "Velocity"}

    second = provider.chat([], tools)
    assert [call.name for call in second.tool_calls] == ["get_product_performance"]
    assert second.tool_calls[0].arguments == {"product_id": 1, "days": 30}

    draft = provider.chat([], None)
    assert draft.content == DRAFT
    assert not draft.tool_calls

    third = provider.chat([], tools)
    assert third.content == _sal_06().oracle_answer
    assert not third.tool_calls


def test_lazy_never_calls_tools() -> None:
    response = LazyProvider().chat([], [{"x": 1}])
    assert response.content == "I am not sure."
    assert not response.tool_calls
