"""Analytics tools: get_sales_summary, get_best_sellers, get_product_performance."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.domain import BestSellersResult, ProductPerformance, SalesSummary
from app.tools.registry import ToolAccess, ToolContext, ToolRegistry

CATEGORY = "analytics"


class PeriodInput(BaseModel):
    days: int = Field(default=30, ge=1, le=365, description="Look-back window in days.")


class BestSellersInput(PeriodInput):
    limit: int = Field(default=10, ge=1, le=50)


class PerformanceInput(PeriodInput):
    product_id: int = Field(ge=1, description="Product to report on.")


def register(reg: ToolRegistry) -> None:
    @reg.tool(
        name="get_sales_summary",
        description=(
            "Store-wide sales for a period: order count, units, revenue, average "
            "order value and a per-category breakdown."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=PeriodInput,
        output_model=SalesSummary,
    )
    def get_sales_summary(ctx: ToolContext, params: PeriodInput) -> SalesSummary:
        return ctx.provider.get_sales_summary(days=params.days)

    @reg.tool(
        name="get_best_sellers",
        description="Top selling products by units sold over a period.",
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=BestSellersInput,
        output_model=BestSellersResult,
    )
    def get_best_sellers(ctx: ToolContext, params: BestSellersInput) -> BestSellersResult:
        products = ctx.provider.get_best_sellers(days=params.days, limit=params.limit)
        return BestSellersResult(
            period_days=params.days, count=len(products), products=products
        )

    @reg.tool(
        name="get_product_performance",
        description=(
            "Sales, revenue, stock cover and content score for a single product. "
            "Use it to judge whether a product is worth investing effort in."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=PerformanceInput,
        output_model=ProductPerformance,
    )
    def get_product_performance(
        ctx: ToolContext, params: PerformanceInput
    ) -> ProductPerformance:
        return ctx.provider.get_product_performance(params.product_id, days=params.days)
