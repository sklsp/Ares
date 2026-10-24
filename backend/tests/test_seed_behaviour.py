"""Behavioural tests for app/seed.py (schema bootstrap + demo data seeding).

The seeder is deterministic: a fixed RNG seed plus a frozen "now" make every
machine produce identical counts, quantities and timestamps, so the expected
values below are literals precomputed from the documented algorithm
(random.Random(42), randint(demand*4, demand*9+1) per product).

Read-back tzinfo is session-dependent (SessionLocal uses expire_on_commit=False,
so some rows return their in-memory aware value while others come back naive
from SQLite), so timestamps are normalised with models.as_utc before comparing.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, inspect
from sqlalchemy.orm import Session

from app.db.models import AgentRun, Inventory, Order, Product, as_utc
from app.seed import clear, ensure_schema, main, seed
from app.seed_data import PRODUCTS

# Frozen clock for deterministic timestamps.
FIXED = datetime(2026, 1, 15, 12, 30, 45, tzinfo=UTC)

# Per-product order counts in PRODUCTS order (seed 42).
EXPECTED_COUNTS = [
    40, 36, 27, 20, 8, 17, 15, 20, 15, 23, 31, 26, 27, 18, 28, 26,
    14, 26, 14, 26, 28, 18, 33, 16, 28, 27, 24, 10, 36, 12, 25, 11,
    18, 27, 13, 35, 28, 33, 10, 19, 17, 20,
]
EXPECTED_TOTAL_ORDERS = sum(EXPECTED_COUNTS)  # == 945

# First four orders per product: (quantity, days, hours, minutes back from FIXED).
RUN_001_FIRST = [(1, 35, 7, 14), (1, 13, 21, 47), (2, 11, 18, 27), (1, 11, 6, 14)]
ELE_007_FIRST = [(1, 16, 23, 49), (2, 15, 0, 21), (2, 34, 16, 4), (1, 73, 20, 4)]


def _freeze_clock(monkeypatch) -> None:
    import app.seed as seed_module

    monkeypatch.setattr(seed_module, "utcnow", lambda: FIXED)


def _orders_for(db: Session, sku: str):
    product = db.query(Product).filter(Product.sku == sku).one()
    return (
        db.query(Order)
        .filter(Order.product_id == product.id)
        .order_by(Order.id)
        .all()
    )


def _counts_by_sku(db: Session) -> list[int]:
    """Order counts per product, in products-id order (insertion order)."""
    products = db.query(Product).order_by(Product.id).all()
    return [len(_orders_for(db, p.sku)) for p in products]


def _wipe_catalog(db: Session) -> None:
    for model in (Order, Inventory, Product):
        db.execute(delete(model))
    db.commit()



# --------------------------------------------------------------------------
# seed()
# --------------------------------------------------------------------------
def test_seed_reset_replaces_catalog_with_full_demo(db, monkeypatch):
    _freeze_clock(monkeypatch)

    result = seed(db, reset=True)

    assert result == {"products": 42, "orders": 945}
    skus = {p.sku for p in db.query(Product).all()}
    assert len(skus) == 42
    assert skus == {item.sku for item in PRODUCTS}
    # The conftest demo catalog (22 orders) is gone: full history reseeded.
    assert db.query(Order).count() == 945
    assert db.query(Inventory).count() == 42


def test_seed_reset_false_on_existing_catalog_is_noop(db):
    result = seed(db, reset=False)

    assert result == {"products": 0, "orders": 0}
    # Conftest catalog untouched: 4 products and 12+6+3+1 orders.
    assert db.query(Product).count() == 4
    assert db.query(Order).count() == 22


def test_seed_reset_false_on_empty_catalog_still_seeds(db, monkeypatch):
    _wipe_catalog(db)
    _freeze_clock(monkeypatch)

    result = seed(db, reset=False)

    assert result == {"products": 42, "orders": 945}
    assert db.query(Product).count() == 42


def test_seed_product_rows_match_catalog_data(db, monkeypatch):
    _freeze_clock(monkeypatch)
    seed(db, reset=True)

    products = db.query(Product).order_by(Product.id).all()
    for index, (product, item) in enumerate(zip(products, PRODUCTS, strict=True)):
        assert product.sku == item.sku
        assert product.title == item.title
        assert product.description == item.description
        assert product.price == item.price
        assert product.category == item.category
        assert product.status == "active"
        # created_at = updated_at = now - (180 - index) days.
        expected_ts = FIXED - timedelta(days=180 - index)
        assert as_utc(product.created_at) == expected_ts
        assert as_utc(product.updated_at) == expected_ts
        inventory = db.get(Inventory, product.inventory.id)
        assert inventory.quantity == item.stock
        assert inventory.reorder_point == item.reorder_point
        assert inventory.warehouse == "MAIN"


def test_seed_order_counts_are_deterministic_per_product(db, monkeypatch):
    _freeze_clock(monkeypatch)
    seed(db, reset=True)

    assert _counts_by_sku(db) == EXPECTED_COUNTS


def test_seed_first_orders_of_run_001_match_literals(db, monkeypatch):
    _freeze_clock(monkeypatch)
    seed(db, reset=True)

    orders = _orders_for(db, "RUN-001")[:4]
    assert [o.quantity for o in orders] == [q for q, _, _, _ in RUN_001_FIRST]
    # price 139.00: totals are round(139 * qty, 2).
    assert [o.total for o in orders] == [139.0, 139.0, 278.0, 139.0]
    expected_ts = [
        FIXED - timedelta(days=d, hours=h, minutes=m)
        for _, d, h, m in RUN_001_FIRST
    ]
    assert [as_utc(o.created_at) for o in orders] == expected_ts


def test_seed_first_orders_of_ele_007_match_literals(db, monkeypatch):
    _freeze_clock(monkeypatch)
    seed(db, reset=True)

    orders = _orders_for(db, "ELE-007")[:4]
    assert [o.quantity for o in orders] == [q for q, _, _, _ in ELE_007_FIRST]
    # price 39.00: totals are round(39 * qty, 2).
    assert [o.total for o in orders] == [39.0, 78.0, 78.0, 39.0]
    expected_ts = [
        FIXED - timedelta(days=d, hours=h, minutes=m)
        for _, d, h, m in ELE_007_FIRST
    ]
    assert [as_utc(o.created_at) for o in orders] == expected_ts


def test_seed_order_timestamps_stay_within_ninety_days(db, monkeypatch):
    _freeze_clock(monkeypatch)
    seed(db, reset=True)

    created = [o.created_at for o in db.query(Order).all()]
    assert len(created) == 945
    # days is randint(0, 90) plus up to 23h59m of extra offset.
    floor = FIXED - timedelta(days=90, hours=23, minutes=59)
    assert all(floor <= as_utc(ts) <= FIXED for ts in created)


def test_seed_is_repeatable_with_identical_counts(db, monkeypatch):
    _freeze_clock(monkeypatch)
    seed(db, reset=True)
    first = _counts_by_sku(db)

    seed(db, reset=True)

    assert _counts_by_sku(db) == first == EXPECTED_COUNTS


# --------------------------------------------------------------------------
# clear()
# --------------------------------------------------------------------------
def test_clear_removes_products_orders_inventory_and_agent_runs(db):
    # Conftest already seeded the demo catalog (4 products, 22 orders).
    db.add(AgentRun(user_request="probe run"))
    db.commit()

    clear(db)

    assert db.query(Product).count() == 0
    assert db.query(Order).count() == 0
    assert db.query(Inventory).count() == 0
    assert db.query(AgentRun).count() == 0


# --------------------------------------------------------------------------
# ensure_schema()
# --------------------------------------------------------------------------
def _patch_alembic(monkeypatch, upgrade_calls, stamp_calls):
    import alembic.command as alembic_command

    def fake_upgrade(config, revision):
        upgrade_calls.append((config, revision))

    def fake_stamp(config, revision):
        stamp_calls.append((config, revision))

    monkeypatch.setattr(alembic_command, "upgrade", fake_upgrade)
    monkeypatch.setattr(alembic_command, "stamp", fake_stamp)


def test_ensure_schema_existing_tables_runs_upgrade_not_stamp(db, monkeypatch):
    upgrade_calls: list = []
    stamp_calls: list = []
    _patch_alembic(monkeypatch, upgrade_calls, stamp_calls)

    ensure_schema()

    # The shared test DB already has the products table -> alembic upgrade path.
    assert len(upgrade_calls) == 1
    config, revision = upgrade_calls[0]
    assert revision == "head"
    from app.config import settings

    assert config.get_main_option("sqlalchemy.url") == settings.database_url
    assert str(config.config_file_name).endswith("alembic.ini")
    assert stamp_calls == []


def test_ensure_schema_empty_db_creates_all_and_stamps(monkeypatch, tmp_path):
    fresh_engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        monkeypatch.setattr("app.seed.engine", fresh_engine)
        upgrade_calls: list = []
        stamp_calls: list = []
        _patch_alembic(monkeypatch, upgrade_calls, stamp_calls)

        ensure_schema()

        # No products table -> create_all path, then alembic stamp head.
        assert inspect(fresh_engine).has_table("products")
        assert len(stamp_calls) == 1
        _, revision = stamp_calls[0]
        assert revision == "head"
        assert upgrade_calls == []
    finally:
        fresh_engine.dispose()


def test_ensure_schema_stamp_failure_is_swallowed(monkeypatch, tmp_path):
    import alembic.command as alembic_command

    fresh_engine = create_engine(f"sqlite:///{tmp_path / 'fresh2.db'}")
    try:
        monkeypatch.setattr("app.seed.engine", fresh_engine)

        def boom(config, revision):
            raise RuntimeError("boom")

        monkeypatch.setattr(alembic_command, "stamp", boom)
        upgrade_calls: list = []
        monkeypatch.setattr(alembic_command, "upgrade", lambda *a: upgrade_calls.append(a))

        # Must not raise even though stamp fails.
        ensure_schema()

        assert inspect(fresh_engine).has_table("products")
    finally:
        fresh_engine.dispose()


# --------------------------------------------------------------------------
# main() CLI entry point
# --------------------------------------------------------------------------
def _patch_ensure_schema(monkeypatch):
    calls: list = []
    monkeypatch.setattr("app.seed.ensure_schema", lambda: calls.append(1))
    return calls


def test_main_without_keep_reseeds_and_prints(db, monkeypatch, capsys):
    ensure_calls = _patch_ensure_schema(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["seed"])

    main()

    out = capsys.readouterr().out
    assert "Seeded 42 products and 945 orders" in out
    assert len(ensure_calls) == 1
    assert db.query(Product).count() == 42


def test_main_with_keep_on_seeded_catalog_prints_zero(db, monkeypatch, capsys):
    _patch_ensure_schema(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["seed", "--keep"])

    main()

    out = capsys.readouterr().out
    assert "Seeded 0 products and 0 orders" in out
    # --keep on a non-empty catalog leaves the conftest demo data alone.
    assert db.query(Product).count() == 4


def test_main_with_keep_on_empty_catalog_still_seeds(db, monkeypatch, capsys):
    _wipe_catalog(db)
    _patch_ensure_schema(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["seed", "--keep"])

    main()

    out = capsys.readouterr().out
    assert "Seeded 42 products and 945 orders" in out
    assert db.query(Product).count() == 42
