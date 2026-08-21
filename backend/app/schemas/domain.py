"""Pydantic models shared by the REST API and the tool layer.

Tool outputs are deliberately compact: every field here ends up in the LLM
context window, so list results carry truncated descriptions and the caller
controls `limit`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ORM = ConfigDict(from_attributes=True)

DESCRIPTION_PREVIEW_CHARS = 220


def preview(text: str, limit: int = DESCRIPTION_PREVIEW_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------
class ProductSummary(BaseModel):
    """Compact product shape used in list results."""

    id: int
    sku: str
    title: str
    category: str
    price: float
    status: str
    inventory_quantity: int
    description_preview: str
    description_word_count: int


class ProductDetail(BaseModel):
    model_config = ORM

    id: int
    sku: str
    title: str
    description: str
    category: str
    price: float
    status: str
    inventory_quantity: int
    reorder_point: int | None = None
    created_at: datetime
    updated_at: datetime


class ProductListResult(BaseModel):
    count: int = Field(description="Number of products in this result")
    total_matching: int = Field(description="Total products matching before limit")
    products: list[ProductSummary]


class ProductPage(BaseModel):
    """REST pagination envelope (not used by tools)."""

    items: list[ProductDetail]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
class InventoryItem(BaseModel):
    product_id: int
    sku: str
    title: str
    category: str
    quantity: int
    reorder_point: int
    warehouse: str
    below_reorder_point: bool


class InventoryResult(BaseModel):
    count: int
    total_units: int
    items: list[InventoryItem]


# --------------------------------------------------------------------------
# Analytics
# --------------------------------------------------------------------------
class CategorySales(BaseModel):
    category: str
    units_sold: int
    revenue: float
    order_count: int


class SalesSummary(BaseModel):
    period_days: int
    order_count: int
    units_sold: int
    total_revenue: float
    average_order_value: float
    by_category: list[CategorySales]


class BestSeller(BaseModel):
    product_id: int
    sku: str
    title: str
    category: str
    units_sold: int
    revenue: float
    inventory_quantity: int


class BestSellersResult(BaseModel):
    period_days: int
    count: int
    products: list[BestSeller]


class ProductPerformance(BaseModel):
    product_id: int
    sku: str
    title: str
    category: str
    price: float
    period_days: int
    order_count: int
    units_sold: int
    revenue: float
    inventory_quantity: int
    days_of_stock_remaining: float | None = None
    content_score: int


# --------------------------------------------------------------------------
# Content quality
# --------------------------------------------------------------------------
Grade = Literal["excellent", "good", "weak", "poor"]


class ContentAnalysis(BaseModel):
    product_id: int
    sku: str
    title: str
    score: int = Field(ge=0, le=100, description="Content quality score, 100 is best")
    grade: Grade
    issues: list[str]
    suggestions: list[str]
    description_word_count: int
    title_word_count: int


class ContentAnalysisResult(BaseModel):
    analyzed: int
    average_score: float
    weakest_first: bool = True
    products: list[ContentAnalysis]


class GeneratedContent(BaseModel):
    product_id: int
    sku: str
    field: Literal["description", "title"]
    current_value: str
    proposed_value: str
    rationale: str
    current_score: int
    proposed_score: int


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
class ProductUpdateResult(BaseModel):
    product_id: int
    sku: str
    updated_fields: list[str]
    before: dict[str, Any]
    after: dict[str, Any]


# --------------------------------------------------------------------------
# Agent introspection
# --------------------------------------------------------------------------
class AgentStepOut(BaseModel):
    model_config = ORM

    id: int
    step_number: int
    step_type: str
    message: str
    tool_name: str | None = None
    input: dict[str, Any] | None = None
    output: Any | None = None
    status: str
    duration_ms: int | None = None
    created_at: datetime


class ApprovalOut(BaseModel):
    model_config = ORM

    id: int
    agent_run_id: int
    tool_name: str
    payload: dict[str, Any]
    preview: dict[str, Any] | None = None
    summary: str
    status: str
    decision_note: str | None = None
    result: Any | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class AgentRunSummary(BaseModel):
    model_config = ORM

    id: int
    session_id: str
    user_request: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    final_response: str | None = None
    error: str | None = None
    tools_used: list[str] = []
    step_count: int = 0
    pending_approvals: int = 0


class AgentRunDetail(AgentRunSummary):
    steps: list[AgentStepOut] = []
    approvals: list[ApprovalOut] = []


class AgentRunListResult(BaseModel):
    count: int
    runs: list[AgentRunSummary]


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------
class ToolInfo(BaseModel):
    name: str
    description: str
    category: str
    access: Literal["read", "write"]
    requires_approval: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class AnalyticsOverview(BaseModel):
    product_count: int
    active_product_count: int
    category_count: int
    total_inventory_units: int
    low_stock_count: int
    out_of_stock_count: int
    average_content_score: float
    weak_content_count: int
    sales: SalesSummary
    top_sellers: list[BestSeller]
    agent_runs_total: int
    agent_runs_pending_approval: int
