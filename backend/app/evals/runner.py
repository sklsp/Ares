"""Run one eval task through the real agent engine."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app.agent import runner as agent_runner
from app.db.models import AgentRun, Product
from app.evals.scoring import RunOutcome
from app.evals.tasks import EvalTask
from app.llm.base import LLMProvider
from app.llm.factory import set_llm_override
from app.seed import seed
from app.tools import ToolContext, get_registry
from app.tools.registry import ToolError

# Tools that reach outside the sandbox (network, live market data) are
# disabled for evals so every run is deterministic and offline.
_DISABLED_TOOLS = ("crawl_website", "discover_stores", "research_market")


def _disabled(ctx: ToolContext, params: Any) -> NoReturn:
    raise ToolError("disabled during evals")


@contextlib.contextmanager
def offline_tools() -> Iterator[None]:
    """Swap the sandbox-escaping tools for stubs that refuse to run.

    The originals are restored on exit, even if the block raises.
    """
    registry = get_registry()
    originals = {name: registry.get(name).fn for name in _DISABLED_TOOLS}
    for tool in originals:
        registry.get(tool).fn = _disabled
    try:
        yield
    finally:
        for name, fn in originals.items():
            registry.get(name).fn = fn


def seed_demo(db: Session) -> None:
    """Replace the database contents with the demo catalog."""
    seed(db, reset=True)


def known_skus(db: Session) -> set[str]:
    return {product.sku for product in db.query(Product).all()}


def sku_resolver(db: Session) -> Callable[[str], int]:
    """A resolver mapping a SKU to its product id (for "@id:" placeholders)."""
    ids = dict(db.query(Product.sku, Product.id).all())

    def resolve(sku: str) -> int:
        try:
            return ids[sku]
        except KeyError:
            raise KeyError(f"unknown SKU {sku}") from None

    return resolve


def run_task(db: Session, task: EvalTask, provider: LLMProvider) -> RunOutcome:
    """Execute one eval task through the real engine and capture what happened."""
    with offline_tools():
        set_llm_override(provider)
        try:
            run = agent_runner.create_run(db, task.prompt)
            agent_runner.execute_run(run.id)
            db.expire_all()
            fresh = db.get(AgentRun, run.id)
            if fresh is None:  # pragma: no cover - the run was just created above
                raise RuntimeError(f"run {run.id} disappeared")
            tools_called = [
                step.tool_name
                for step in fresh.steps
                if step.step_type in ("tool_call", "approval_request") and step.tool_name
            ]
            return RunOutcome(
                tools_called=tools_called,
                status=fresh.status,
                answer=fresh.final_response or "",
                duration_ms=fresh.duration_ms,
            )
        finally:
            set_llm_override(None)
