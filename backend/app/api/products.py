"""Product CRUD for the dashboard.

These are direct human edits, so they apply immediately - the approval gate
exists for changes the *agent* proposes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import DbSession, Provider
from app.integrations.base import ProductNotFoundError, ProviderError
from app.schemas.api import ProductUpdateRequest
from app.schemas.domain import ContentAnalysis, ProductDetail, ProductPage
from app.services.content_quality import analyze_product

router = APIRouter(prefix="/products", tags=["products"])


class ProductWithQuality(ProductDetail):
    content: ContentAnalysis


class ProductQualityPage(ProductPage):
    items: list[ProductWithQuality]
    categories: list[str]


def _with_quality(detail: ProductDetail) -> ProductWithQuality:
    return ProductWithQuality(**detail.model_dump(), content=analyze_product(detail))


@router.get("", response_model=ProductQualityPage)
def list_products(
    provider: Provider,
    db: DbSession,
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ProductQualityPage:
    products, total = provider.get_products(
        category=category, status=status, search=search, limit=limit, offset=offset
    )
    return ProductQualityPage(
        items=[_with_quality(p) for p in products],
        total=total,
        limit=limit,
        offset=offset,
        categories=provider.list_categories(),
    )


@router.get("/{product_id}", response_model=ProductWithQuality)
def get_product(product_id: int, provider: Provider) -> ProductWithQuality:
    try:
        return _with_quality(provider.get_product(product_id))
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{product_id}", response_model=ProductWithQuality)
def update_product(
    product_id: int, payload: ProductUpdateRequest, provider: Provider
) -> ProductWithQuality:
    changes = payload.changed_fields()
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        provider.update_product(product_id, changes)
        return _with_quality(provider.get_product(product_id))
    except ProductNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
