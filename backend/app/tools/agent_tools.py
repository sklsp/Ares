"""Agent introspection tools: get_agent_run, get_recent_agent_runs.

These let the agent answer questions about its own history, e.g.
"what did you change today?".
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.domain import AgentRunDetail, AgentRunListResult
from app.services import runs as run_service
from app.tools.registry import ToolAccess, ToolContext, ToolError, ToolRegistry

CATEGORY = "agent"


class GetRunInput(BaseModel):
    run_id: int = Field(ge=1, description="Id of the agent run to inspect.")


class RecentRunsInput(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    status: str = Field(
        default="",
        description="Filter by RUNNING, WAITING_FOR_APPROVAL, COMPLETED or FAILED.",
    )


def register(reg: ToolRegistry) -> None:
    @reg.tool(
        name="get_agent_run",
        description="Full detail of one past agent run: every step, tool call and approval.",
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=GetRunInput,
        output_model=AgentRunDetail,
    )
    def get_agent_run(ctx: ToolContext, params: GetRunInput) -> AgentRunDetail:
        run = run_service.get_run(ctx.db, params.run_id)
        if run is None:
            raise ToolError(f"Agent run {params.run_id} does not exist")
        return run_service.to_detail(run)

    @reg.tool(
        name="get_recent_agent_runs",
        description=(
            "Recent agent runs with their status, duration and the tools each one used. "
            "Use this to report on what the agent has been doing."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=RecentRunsInput,
        output_model=AgentRunListResult,
    )
    def get_recent_agent_runs(
        ctx: ToolContext, params: RecentRunsInput
    ) -> AgentRunListResult:
        rows = run_service.list_runs(
            ctx.db, limit=params.limit, status=params.status or None
        )
        summaries = [run_service.to_summary(r) for r in rows]
        return AgentRunListResult(count=len(summaries), runs=summaries)
