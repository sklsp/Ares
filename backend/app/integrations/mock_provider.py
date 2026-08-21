"""PostgreSQL-backed implementation of `EcommerceProvider`.

This is the "store" the demo runs against. It behaves like a real platform
API - partial updates, not-found errors, aggregated sales - but the data
lives in our own database so the project needs no Shopify credentials.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Inventory, Order, Product, utcnow
from app.integrations.base import (
    EcommerceProvider,
    ProductNotFoundError,
    ProviderError,
)
from app.schemas.domain import (
    BestSeller,
    CategorySales,
    InventoryItem,
    ProductDetail,
    ProductPerformance,
    ProductUpdateResult,
    SalesSummary,
)
from app.services.content_quality import score_content

UPDATABLE_FIELDS = ("title", "description", "price", "category", "status")


def to_detail(product: Product) -> ProductDetail:
    return ProductDetail(
        id=product.id,
        sku=product.sku,
        title=product.title,
        description=product.description,
        category=product.category,
        price=product.price,
        status=product.status,
        inventory_quantity=product.inventory_quantity,
        reorder_point=product.inventory.reorder_point if product.inventory else None,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


class MockEcommerceProvider(EcommerceProvider):
    name = "mock"

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- catalog --------------------------------------------------------
    def _base_query(self):
        return select(Product).options(selectinload(Product.inventory))

    def get_products(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[ProductDetail], int]:
        stmt = self._base_query()
        count_stmt = select(func.count()).select_from(Product)

        if category:
            cond = func.lower(Product.category) == category.lower()
            stmt, count_stmt = stmt.where(cond), count_stmt.where(cond)
        if status:
            cond = Product.status == status
            stmt, count_stmt = stmt.where(cond), count_stmt.where(cond)
        if search:
            pattern = f"%{search.lower()}%"
            cond = func.lower(Product.title).like(pattern) | func.lower(
                Product.description
            ).like(pattern) | func.lower(Product.sku).like(pattern)
            stmt, count_stmt = stmt.where(cond), count_stmt.where(cond)

        total = self.db.execute(count_stmt).scalar_one()
        stmt = stmt.order_by(Product.id).limit(limit).offset(offset)
        rows = self.db.execute(stmt).scalars().all()
        return [to_detail(p) for p in rows], total

    def _get_row(self, product_id: int) -> Product:
        product = self.db.execute(
            self._base_query().where(Product.id == product_id)
        ).scalar_one_or_none()
        if product is None:
            raise ProductNotFoundError(product_id)
        return product

    def get_product(self, product_id: int) -> ProductDetail:
        return to_detail(self._get_row(product_id))

    def get_product_by_sku(self, sku: str) -> ProductDetail:
        product = self.db.execute(
            self._base_query().where(func.lower(Product.sku) == sku.lower())
        ).scalar_one_or_none()
        if product is None:
            raise ProductNotFoundError(sku)
        return to_detail(product)

    def update_product(self, product_id: int, fields: dict[str, Any]) -> ProductUpdateResult:
        product = self._get_row(product_id)

        unknown = set(fields) - set(UPDATABLE_FIELDS)
        if unknown:
            raise ProviderError(f"Unsupported fields: {', '.join(sorted(unknown))}")

        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for key, value in fields.items():
            if value is None:
                continue
            current = getattr(product, key)
            if current == value:
                continue
            before[key] = current
            after[key] = value
            setattr(product, key, value)

        if after:
            product.updated_at = utcnow()
            self.db.commit()
            self.db.refresh(product)

        return ProductUpdateResult(
            product_id=product.id,
            sku=product.sku,
            updated_fields=sorted(after),
            before=before,
            after=after,
        )

    def list_categories(self) -> list[str]:
        rows = self.db.execute(
            select(Product.category).distinct().order_by(Product.category)
        ).scalars()
        return list(rows)

    # --- inventory ------------------------------------------------------
    def get_inventory(
        self, *, only_below_reorder: bool = False, limit: int = 50
    ) -> list[InventoryItem]:
        stmt = select(Inventory, Product).join(Product, Inventory.product_id == Product.id)
        if only_below_reorder:
            stmt = stmt.where(Inventory.quantity <= Inventory.reorder_point)
        stmt = stmt.order_by(Inventory.quantity.asc(), Product.id).limit(limit)

        return [
            InventoryItem(
                product_id=product.id,
                sku=product.sku,
                title=product.title,
                category=product.category,
                quantity=inv.quantity,
                reorder_point=inv.reorder_point,
                warehouse=inv.warehouse,
                below_reorder_point=inv.quantity <= inv.reorder_point,
            )
            for inv, product in self.db.execute(stmt).all()
        ]

    # --- sales ----------------------------------------------------------
    def _since(self, days: int):
        return utcnow() - timedelta(days=days)

    def get_sales_summary(self, *, days: int = 30) -> SalesSummary:
        since = self._since(days)
        totals = self.db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.quantity), 0),
                func.coalesce(func.sum(Order.total), 0.0),
            ).where(Order.created_at >= since)
        ).one()
        order_count, units, revenue = int(totals[0]), int(totals[1]), float(totals[2])

        by_category = [
            CategorySales(
                category=row[0],
                units_sold=int(row[1]),
                revenue=round(float(row[2]), 2),
                order_count=int(row[3]),
            )
            for row in self.db.execute(
                select(
                    Product.category,
                    func.coalesce(func.sum(Order.quantity), 0),
                    func.coalesce(func.sum(Order.total), 0.0),
                    func.count(Order.id),
                )
                .join(Product, Order.product_id == Product.id)
                .where(Order.created_at >= since)
                .group_by(Product.category)
                .order_by(func.sum(Order.total).desc())
            ).all()
        ]

        return SalesSummary(
            period_days=days,
            order_count=order_count,
            units_sold=units,
            total_revenue=round(revenue, 2),
            average_order_value=round(revenue / order_count, 2) if order_count else 0.0,
            by_category=by_category,
        )

    def get_best_sellers(self, *, days: int = 30, limit: int = 10) -> list[BestSeller]:
        since = self._since(days)
        rows = self.db.execute(
            select(
                Product,
                func.coalesce(func.sum(Order.quantity), 0).label("units"),
                func.coalesce(func.sum(Order.total), 0.0).label("revenue"),
            )
            .join(Order, Order.product_id == Product.id)
            .where(Order.created_at >= since)
            .group_by(Product.id)
            .order_by(func.sum(Order.quantity).desc())
            .limit(limit)
        ).all()

        return [
            BestSeller(
                product_id=product.id,
                sku=product.sku,
                title=product.title,
                category=product.category,
                units_sold=int(units),
                revenue=round(float(revenue), 2),
                inventory_quantity=product.inventory_quantity,
            )
            for product, units, revenue in rows
        ]

    def get_product_performance(self, product_id: int, *, days: int = 30) -> ProductPerformance:
        product = self._get_row(product_id)
        since = self._since(days)
        order_count, units, revenue = self.db.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.quantity), 0),
                func.coalesce(func.sum(Order.total), 0.0),
            ).where(Order.product_id == product_id, Order.created_at >= since)
        ).one()

        units = int(units)
        daily_rate = units / days if days else 0.0
        stock = product.inventory_quantity
        score, *_ = score_content(product.title, product.description)

        return ProductPerformance(
            product_id=product.id,
            sku=product.sku,
            title=product.title,
            category=product.category,
            price=product.price,
            period_days=days,
            order_count=int(order_count),
            units_sold=units,
            revenue=round(float(revenue), 2),
            inventory_quantity=stock,
            days_of_stock_remaining=round(stock / daily_rate, 1) if daily_rate else None,
            content_score=score,
        )
