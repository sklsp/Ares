"""Run scheduling.

Agent runs are slow (LLM calls) so the API never executes one inside the
request. `POST /agent/run` persists the run, hands it to a small worker pool
and returns immediately; the UI follows along over SSE. Each worker opens its
own database session - sessions are not shared across threads.
"""

from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.agent.engine import AgentEngine
from app.config import settings
from app.db.base import SessionLocal
from app.db.models import AgentRun, RunStatus
from app.integrations.mock_provider import MockEcommerceProvider
from app.llm.factory import build_llm_provider
from app.logging_config import get_logger
from app.tools import get_registry

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(
    max_workers=settings.agent_max_workers, thread_name_prefix="agent"
)
atexit.register(lambda: _executor.shutdown(wait=False, cancel_futures=True))


def build_engine(db: Session) -> AgentEngine:
    return AgentEngine(
        db=db,
        llm=build_llm_provider(settings),
        registry=get_registry(),
        provider=MockEcommerceProvider(db),
        settings=settings,
    )


def create_run(db: Session, message: str, session_id: str = "default") -> AgentRun:
    run = AgentRun(
        user_request=message.strip(),
        session_id=session_id,
        status=RunStatus.RUNNING.value,
        messages=[],
        pending_tool_calls=[],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _with_run(run_id: int, action: str) -> None:
    """Open a fresh session and start or resume the run inside it."""
    db = SessionLocal()
    try:
        run = db.get(AgentRun, run_id)
        if run is None:
            logger.error("Cannot %s run %s: not found", action, run_id)
            return
        engine = build_engine(db)
        if action == "start":
            engine.start(run)
        else:
            engine.resume(run)
    except Exception:  # noqa: BLE001 - a worker must never die silently
        logger.exception("Worker crashed handling run %s", run_id)
        db.rollback()
    finally:
        db.close()


def execute_run(run_id: int) -> None:
    """Run the agent synchronously on the calling thread."""
    _with_run(run_id, "start")


def resume_run(run_id: int) -> None:
    """Continue a run whose approval was just resolved."""
    _with_run(run_id, "resume")


def submit_run(run_id: int) -> None:
    if settings.agent_run_inline:
        execute_run(run_id)
    else:
        _executor.submit(execute_run, run_id)


def submit_resume(run_id: int) -> None:
    if settings.agent_run_inline:
        resume_run(run_id)
    else:
        _executor.submit(resume_run, run_id)


def shutdown() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
