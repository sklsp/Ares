"""The e-commerce platform boundary.

Every tool talks to the store through this interface, never to SQLAlchemy
directly. That is what makes `MockEcommerceProvider` swappable for a real
`ShopifyProvider` later without touching the agent or the tool registry:
the interface trades in Pydantic DTOs, not ORM rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.schemas.domain import (
    BestSeller,
    InventoryItem,
    ProductDetail,
    ProductPerformance,
    ProductUpdateResult,
    SalesSummary,
)


class ProviderError(RuntimeError):
    """Raised when the backing store rejects an operation."""


class ProductNotFoundError(ProviderError):
    def __init__(self, identifier: Any) -> None:
        super().__init__(f"Product not found: {identifier}")
        self.identifier = identifier


class EcommerceProvider(ABC):
    """Read/write access to a store's catalog, inventory and sales."""

    name: str = "abstract"

    # --- catalog --------------------------------------------------------
    @abstractmethod
    def get_products(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[ProductDetail], int]:
        """Return a page of products and the total number of matches."""

    @abstractmethod
    def get_product(self, product_id: int) -> ProductDetail:
        """Return one product or raise ProductNotFoundError."""

    @abstractmethod
    def get_product_by_sku(self, sku: str) -> ProductDetail:
        """Return one product by SKU or raise ProductNotFoundError."""

    @abstractmethod
    def update_product(self, product_id: int, fields: dict[str, Any]) -> ProductUpdateResult:
        """Apply a partial update and report what actually changed."""

    @abstractmethod
    def list_categories(self) -> list[str]:
        ...

    # --- inventory ------------------------------------------------------
    @abstractmethod
    def get_inventory(
        self, *, only_below_reorder: bool = False, limit: int = 50
    ) -> list[InventoryItem]:
        ...

    # --- sales ----------------------------------------------------------
    @abstractmethod
    def get_sales_summary(self, *, days: int = 30) -> SalesSummary:
        ...

    @abstractmethod
    def get_best_sellers(self, *, days: int = 30, limit: int = 10) -> list[BestSeller]:
        ...

    @abstractmethod
    def get_product_performance(self, product_id: int, *, days: int = 30) -> ProductPerformance:
        ...
