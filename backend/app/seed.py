"""Load the demo store.

    python -m app.seed          # reset and reseed
    python -m app.seed --keep   # only seed if the catalog is empty

Order history is generated from each product's `demand` weight with a fixed
random seed, so every machine gets the same numbers and the README examples
stay true.
"""

from __future__ import annotations

import argparse
import random
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import Base, SessionLocal, engine
from app.db.models import AgentRun, Inventory, Order, Product, utcnow
from app.db import identity  # noqa: F401 - registers the users, sessions and audit tables for create_all
from app.logging_config import configure_logging, get_logger
from app.seed_data import PRODUCTS

logger = get_logger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
ORDER_HISTORY_DAYS = 90
RANDOM_SEED = 42


def ensure_schema() -> None:
    """Create tables if the database is empty, and mark it as migrated.

    Stamping alembic keeps `alembic upgrade head` working afterwards instead
    of failing on tables that already exist.
    """
    if inspect(engine).has_table("products"):
        from alembic.config import Config
        from alembic import command

        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", settings.database_url)
        command.upgrade(config, "head")
        return

    logger.info("Creating schema")
    Base.metadata.create_all(engine)
    try:
        from alembic.config import Config

        from alembic import command

        config = Config(str(BACKEND_DIR / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", settings.database_url)
        command.stamp(config, "head")
    except Exception as exc:  # noqa: BLE001 - alembic is optional for a quick start
        logger.warning("Could not stamp alembic revision: %s", exc)


def clear(db: Session) -> None:
    for model in (Order, Inventory, Product, AgentRun):
        db.execute(delete(model))
    db.commit()


def seed(db: Session, *, reset: bool = True) -> dict[str, int]:
    existing = db.execute(select(Product.id).limit(1)).first()
    if existing and not reset:
        logger.info("Catalog already seeded, nothing to do")
        return {"products": 0, "orders": 0}

    if reset:
        clear(db)

    rng = random.Random(RANDOM_SEED)
    now = utcnow()
    order_count = 0

    for index, item in enumerate(PRODUCTS):
        product = Product(
            sku=item.sku,
            title=item.title,
            description=item.description,
            price=item.price,
            category=item.category,
            status="active",
            created_at=now - timedelta(days=180 - index),
            updated_at=now - timedelta(days=180 - index),
        )
        product.inventory = Inventory(
            quantity=item.stock,
            reorder_point=item.reorder_point,
            warehouse="MAIN",
        )
        db.add(product)
        db.flush()  # assign product.id

        # Demand 0-5 maps to roughly 0-45 orders over the window.
        n_orders = rng.randint(item.demand * 4, item.demand * 9 + 1)
        for _ in range(n_orders):
            quantity = rng.choices([1, 1, 1, 2, 3], weights=[6, 5, 4, 3, 1])[0]
            db.add(
                Order(
                    product_id=product.id,
                    quantity=quantity,
                    total=round(product.price * quantity, 2),
                    created_at=now
                    - timedelta(
                        days=rng.randint(0, ORDER_HISTORY_DAYS),
                        hours=rng.randint(0, 23),
                        minutes=rng.randint(0, 59),
                    ),
                )
            )
            order_count += 1

    db.commit()
    logger.info("Seeded %d products and %d orders", len(PRODUCTS), order_count)
    return {"products": len(PRODUCTS), "orders": order_count}


def main() -> None:
    configure_logging(settings.log_level)
    parser = argparse.ArgumentParser(description="Seed the demo store")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave existing data alone if the catalog is not empty",
    )
    args = parser.parse_args()

    ensure_schema()
    db = SessionLocal()
    try:
        result = seed(db, reset=not args.keep)
        print(f"Seeded {result['products']} products and {result['orders']} orders")
    finally:
        db.close()


if __name__ == "__main__":
    main()
