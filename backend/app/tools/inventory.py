"""Inventory tools: get_inventory, get_low_stock_products."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.domain import InventoryResult
from app.tools.registry import ToolAccess, ToolContext, ToolRegistry

CATEGORY = "inventory"


class GetInventoryInput(BaseModel):
    limit: int = Field(default=50, ge=1, le=200, description="Maximum rows to return.")


class LowStockInput(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    threshold: int = Field(
        default=0,
        ge=0,
        description=(
            "Optional absolute stock threshold. When 0, each product's own "
            "reorder point is used instead."
        ),
    )


def register(reg: ToolRegistry) -> None:
    @reg.tool(
        name="get_inventory",
        description="Current stock levels for all products, lowest stock first.",
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=GetInventoryInput,
        output_model=InventoryResult,
    )
    def get_inventory(ctx: ToolContext, params: GetInventoryInput) -> InventoryResult:
        items = ctx.provider.get_inventory(limit=params.limit)
        return InventoryResult(
            count=len(items),
            total_units=sum(i.quantity for i in items),
            items=items,
        )

    @reg.tool(
        name="get_low_stock_products",
        description=(
            "Products that need restocking: stock at or below their reorder point, "
            "or below an explicit threshold if one is given."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=LowStockInput,
        output_model=InventoryResult,
    )
    def get_low_stock_products(ctx: ToolContext, params: LowStockInput) -> InventoryResult:
        if params.threshold:
            items = [
                i
                for i in ctx.provider.get_inventory(limit=200)
                if i.quantity <= params.threshold
            ][: params.limit]
        else:
            items = ctx.provider.get_inventory(only_below_reorder=True, limit=params.limit)

        return InventoryResult(
            count=len(items),
            total_units=sum(i.quantity for i in items),
            items=items,
        )
