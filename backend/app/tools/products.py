"""Product tools: get_products, get_product, search_products, update_product."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.schemas.domain import (
    ProductDetail,
    ProductListResult,
    ProductSummary,
    ProductUpdateResult,
    preview,
)
from app.services.content_quality import score_content
from app.tools.registry import ToolAccess, ToolContext, ToolError, ToolRegistry

CATEGORY = "products"


def to_summary(detail: ProductDetail) -> ProductSummary:
    return ProductSummary(
        id=detail.id,
        sku=detail.sku,
        title=detail.title,
        category=detail.category,
        price=detail.price,
        status=detail.status,
        inventory_quantity=detail.inventory_quantity,
        description_preview=preview(detail.description),
        description_word_count=len(detail.description.split()),
    )


class GetProductsInput(BaseModel):
    category: str = Field(
        default="", description="Filter by category, e.g. 'Running Shoes'. Empty means all."
    )
    status: str = Field(default="", description="Filter by status: active, draft or archived.")
    limit: int = Field(default=25, ge=1, le=100, description="Maximum products to return.")
    offset: int = Field(default=0, ge=0, description="Number of products to skip.")


class GetProductInput(BaseModel):
    product_id: int = Field(default=0, ge=0, description="Numeric product id.")
    sku: str = Field(default="", description="Product SKU, used when product_id is not known.")

    @model_validator(mode="after")
    def one_identifier(self) -> "GetProductInput":
        if not self.product_id and not self.sku:
            raise ValueError("provide either product_id or sku")
        return self


class SearchProductsInput(BaseModel):
    query: str = Field(min_length=1, description="Free text matched against title, SKU and description.")
    limit: int = Field(default=10, ge=1, le=50)


class UpdateProductInput(BaseModel):
    product_id: int = Field(ge=1, description="Id of the product to update.")
    title: str | None = Field(default=None, description="New title, omit to keep the current one.")
    description: str | None = Field(
        default=None, description="New description, omit to keep the current one."
    )
    price: float | None = Field(default=None, gt=0, description="New price in store currency.")
    category: str | None = Field(default=None, description="New category.")
    status: str | None = Field(default=None, description="active, draft or archived.")

    @model_validator(mode="after")
    def at_least_one_change(self) -> "UpdateProductInput":
        if not any(
            v is not None for v in (self.title, self.description, self.price, self.category, self.status)
        ):
            raise ValueError("provide at least one field to update")
        return self


def register(reg: ToolRegistry) -> None:
    @reg.tool(
        name="get_products",
        description=(
            "List products from the catalog with optional category and status filters. "
            "Returns compact summaries including price, stock and a description preview."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=GetProductsInput,
        output_model=ProductListResult,
    )
    def get_products(ctx: ToolContext, params: GetProductsInput) -> ProductListResult:
        products, total = ctx.provider.get_products(
            category=params.category or None,
            status=params.status or None,
            limit=params.limit,
            offset=params.offset,
        )
        summaries = [to_summary(p) for p in products]
        return ProductListResult(
            count=len(summaries), total_matching=total, products=summaries
        )

    @reg.tool(
        name="get_product",
        description="Fetch one product by id or SKU, including its full description.",
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=GetProductInput,
        output_model=ProductDetail,
    )
    def get_product(ctx: ToolContext, params: GetProductInput) -> ProductDetail:
        if params.product_id:
            return ctx.provider.get_product(params.product_id)
        return ctx.provider.get_product_by_sku(params.sku)

    @reg.tool(
        name="search_products",
        description="Search the catalog by keyword across title, SKU and description.",
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=SearchProductsInput,
        output_model=ProductListResult,
    )
    def search_products(ctx: ToolContext, params: SearchProductsInput) -> ProductListResult:
        products, total = ctx.provider.get_products(search=params.query, limit=params.limit)
        summaries = [to_summary(p) for p in products]
        return ProductListResult(
            count=len(summaries), total_matching=total, products=summaries
        )

    @reg.tool(
        name="update_product",
        description=(
            "Update a product's title, description, price, category or status. "
            "This changes store data and always requires human approval first."
        ),
        category=CATEGORY,
        access=ToolAccess.WRITE,
        input_model=UpdateProductInput,
        output_model=ProductUpdateResult,
    )
    def update_product(ctx: ToolContext, params: UpdateProductInput) -> ProductUpdateResult:
        fields = params.model_dump(exclude_none=True, exclude={"product_id"})
        result = ctx.provider.update_product(params.product_id, fields)
        if not result.updated_fields:
            raise ToolError(
                f"Product {params.product_id} already has those values, nothing was changed."
            )
        return result


def describe_update(detail: ProductDetail, fields: dict) -> dict:
    """Build the before/after preview shown in the approval UI."""
    changes = [
        {"field": key, "current": getattr(detail, key, None), "proposed": value}
        for key, value in fields.items()
        if getattr(detail, key, None) != value
    ]

    scores = None
    if any(c["field"] in ("title", "description") for c in changes):
        after_title = fields.get("title", detail.title)
        after_description = fields.get("description", detail.description)
        scores = {
            "current": score_content(detail.title, detail.description)[0],
            "proposed": score_content(after_title, after_description)[0],
        }

    return {
        "product_id": detail.id,
        "sku": detail.sku,
        "title": detail.title,
        "changes": changes,
        "content_score": scores,
    }
