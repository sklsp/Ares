"""Store analytics for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, Provider
from app.db.models import AgentRun, ApprovalRequest, ApprovalStatus, Inventory, Product
from app.schemas.domain import AnalyticsOverview
from app.services.content_quality import score_content

router = APIRouter(prefix="/analytics", tags=["analytics"])

WEAK_CONTENT_SCORE = 60


@router.get("/summary", response_model=AnalyticsOverview)
def summary(
    db: DbSession,
    provider: Provider,
    days: int = Query(default=30, ge=1, le=365),
) -> AnalyticsOverview:
    products = db.execute(select(Product.title, Product.description)).all()
    scores = [score_content(title, description)[0] for title, description in products]

    product_count = len(products)
    active_count = db.execute(
        select(func.count()).select_from(Product).where(Product.status == "active")
    ).scalar_one()
    category_count = db.execute(
        select(func.count(func.distinct(Product.category)))
    ).scalar_one()
    total_units = (
        db.execute(select(func.coalesce(func.sum(Inventory.quantity), 0))).scalar_one() or 0
    )
    low_stock = db.execute(
        select(func.count())
        .select_from(Inventory)
        .where(Inventory.quantity <= Inventory.reorder_point)
    ).scalar_one()
    out_of_stock = db.execute(
        select(func.count()).select_from(Inventory).where(Inventory.quantity == 0)
    ).scalar_one()

    runs_total = db.execute(select(func.count()).select_from(AgentRun)).scalar_one()
    pending = db.execute(
        select(func.count())
        .select_from(ApprovalRequest)
        .where(ApprovalRequest.status == ApprovalStatus.PENDING.value)
    ).scalar_one()

    return AnalyticsOverview(
        product_count=product_count,
        active_product_count=int(active_count),
        category_count=int(category_count),
        total_inventory_units=int(total_units),
        low_stock_count=int(low_stock),
        out_of_stock_count=int(out_of_stock),
        average_content_score=round(sum(scores) / len(scores), 1) if scores else 0.0,
        weak_content_count=sum(1 for s in scores if s < WEAK_CONTENT_SCORE),
        sales=provider.get_sales_summary(days=days),
        top_sellers=provider.get_best_sellers(days=days, limit=5),
        agent_runs_total=int(runs_total),
        agent_runs_pending_approval=int(pending),
    )
