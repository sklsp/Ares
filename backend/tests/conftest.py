"""Test configuration.

The environment is set before any app module is imported, so the whole suite
runs against a throwaway SQLite file with the agent executing inline and the
LLM replaced by a scripted fake. No Postgres and no Ollama required.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = Path(tempfile.mkdtemp(prefix="ecom-agent-tests-")) / "test.db"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["AGENT_RUN_INLINE"] = "true"
os.environ["AGENT_MAX_ITERATIONS"] = "5"
os.environ["AGENT_MAX_TOOL_CALLS"] = "8"
os.environ["AGENT_TIMEOUT_SECONDS"] = "30"
os.environ["LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db.base import Base, SessionLocal, engine  # noqa: E402
from app.db.models import (  # noqa: E402
    AgentRun,
    Inventory,
    Order,
    Product,
    utcnow,
)
from app.integrations.mock_provider import MockEcommerceProvider  # noqa: E402
from app.llm.factory import set_llm_override  # noqa: E402
from app.main import app  # noqa: E402
from app.tools import ToolContext, get_registry  # noqa: E402
from tests.fakes import FakeLLM  # noqa: E402

# A small deliberate catalog: one strong product, two weak ones, one empty.
CATALOG = [
    dict(
        sku="RUN-001",
        title="Velocity Pro 5 Road Running Shoe",
        description=(
            "The Velocity Pro 5 is a daily trainer for runners logging 40 to 80 km a "
            "week. A 32 mm nitrogen-infused foam midsole returns energy on long efforts "
            "while the 8 mm drop keeps your stride familiar. The engineered mesh upper "
            "weighs 249 g and dries quickly after wet runs. Ideal for tempo sessions and "
            "weekend long runs on tarmac. Machine washable at 30 degrees, air dry only. "
            "The outsole uses a hard-wearing rubber compound across the heel."
        ),
        price=139.0,
        category="Running Shoes",
        stock=80,
        reorder_point=20,
        orders=12,
    ),
    dict(
        sku="RUN-003",
        title="Running Shoes X",
        description="Comfortable running shoes.",
        price=89.0,
        category="Running Shoes",
        stock=110,
        reorder_point=20,
        orders=6,
    ),
    dict(
        sku="GYM-003",
        title="Yoga Mat",
        description="Good product for yoga. High quality product.",
        price=29.0,
        category="Gym Equipment",
        stock=4,
        reorder_point=30,
        orders=3,
    ),
    dict(
        sku="CLO-004",
        title="Compression Leggings",
        description="",
        price=59.0,
        category="Clothing",
        stock=0,
        reorder_point=20,
        orders=1,
    ),
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def db():
    """A clean, freshly seeded database for every test."""
    session = SessionLocal()
    for model in (Order, Inventory, Product, AgentRun):
        session.execute(delete(model))
    session.commit()

    now = utcnow()
    for item in CATALOG:
        product = Product(
            sku=item["sku"],
            title=item["title"],
            description=item["description"],
            price=item["price"],
            category=item["category"],
            status="active",
        )
        product.inventory = Inventory(
            quantity=item["stock"], reorder_point=item["reorder_point"]
        )
        session.add(product)
        session.flush()
        for _ in range(item["orders"]):
            session.add(
                Order(
                    product_id=product.id,
                    quantity=1,
                    total=product.price,
                    created_at=now,
                )
            )
    session.commit()

    yield session
    session.close()


@pytest.fixture
def provider(db):
    return MockEcommerceProvider(db)


@pytest.fixture
def registry():
    return get_registry()


@pytest.fixture
def llm():
    """Scripted model, installed process-wide for the duration of the test."""
    fake = FakeLLM()
    set_llm_override(fake)
    yield fake
    set_llm_override(None)


@pytest.fixture
def ctx(db, provider, llm):
    return ToolContext(db=db, provider=provider, llm=llm)


@pytest.fixture
def client(llm):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def product_id(db):
    """Id of the weakest-copy product (RUN-003)."""
    return db.query(Product).filter(Product.sku == "RUN-003").one().id
