"""MCP server exposing the store tools over stdio.

Same registry, same implementations, same validation as the internal agent -
this file is only a transport. Point any MCP client at it:

    {
      "mcpServers": {
        "ecommerce-ops": {
          "command": "python",
          "args": ["-m", "mcp_server"],
          "cwd": "/absolute/path/to/backend",
          "env": {"DATABASE_URL": "postgresql+psycopg://..."}
        }
      }
    }

Run it directly with:  python -m mcp_server

Note on writes: `update_product` here is a *direct* write. The human-approval
gate lives in the agent engine, and an MCP client is already driven by a human
who sees and confirms each call in their own UI.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from app.config import settings
from app.db.base import SessionLocal
from app.integrations.mock_provider import MockEcommerceProvider
from app.llm.factory import build_llm_provider
from app.logging_config import configure_logging, get_logger
from app.tools import ToolContext, get_registry

logger = get_logger(__name__)

SERVER_NAME = "ecommerce-ops"

TOOL_NAMES = (
    "get_products",
    "get_product",
    "search_products",
    "get_inventory",
    "get_low_stock_products",
    "get_best_sellers",
    "get_sales_summary",
    "analyze_product_content",
    "update_product",
)

READ_ONLY = ToolAnnotations(readOnlyHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)


@contextmanager
def tool_context():
    """One database session per tool call."""
    db = SessionLocal()
    try:
        yield ToolContext(db=db, provider=MockEcommerceProvider(db), llm=build_llm_provider())
    finally:
        db.close()


def call(name: str, **arguments: Any) -> Any:
    """Run a registry tool and return its JSON result."""
    with tool_context() as ctx:
        return get_registry().execute(name, arguments, ctx)


def build_server() -> MCPServer:
    server = MCPServer(
        name=SERVER_NAME,
        version="1.0.0",
        instructions=(
            "Tools for an e-commerce catalog: products, inventory, sales and "
            "content quality. Reads are safe; update_product writes to the store."
        ),
    )
    registry = get_registry()

    def described(name: str) -> str:
        return registry.get(name).description

    @server.tool(
        name="get_products",
        description=described("get_products"),
        annotations=READ_ONLY,
    )
    def get_products(category: str = "", status: str = "", limit: int = 25) -> Any:
        return call("get_products", category=category, status=status, limit=limit)

    @server.tool(
        name="get_product", description=described("get_product"), annotations=READ_ONLY
    )
    def get_product(product_id: int = 0, sku: str = "") -> Any:
        return call("get_product", product_id=product_id, sku=sku)

    @server.tool(
        name="search_products",
        description=described("search_products"),
        annotations=READ_ONLY,
    )
    def search_products(query: str, limit: int = 10) -> Any:
        return call("search_products", query=query, limit=limit)

    @server.tool(
        name="get_inventory", description=described("get_inventory"), annotations=READ_ONLY
    )
    def get_inventory(limit: int = 50) -> Any:
        return call("get_inventory", limit=limit)

    @server.tool(
        name="get_low_stock_products",
        description=described("get_low_stock_products"),
        annotations=READ_ONLY,
    )
    def get_low_stock_products(limit: int = 20, threshold: int = 0) -> Any:
        return call("get_low_stock_products", limit=limit, threshold=threshold)

    @server.tool(
        name="get_best_sellers",
        description=described("get_best_sellers"),
        annotations=READ_ONLY,
    )
    def get_best_sellers(days: int = 30, limit: int = 10) -> Any:
        return call("get_best_sellers", days=days, limit=limit)

    @server.tool(
        name="get_sales_summary",
        description=described("get_sales_summary"),
        annotations=READ_ONLY,
    )
    def get_sales_summary(days: int = 30) -> Any:
        return call("get_sales_summary", days=days)

    @server.tool(
        name="analyze_product_content",
        description=described("analyze_product_content"),
        annotations=READ_ONLY,
    )
    def analyze_product_content(category: str = "", limit: int = 10, max_score: int = 100) -> Any:
        return call(
            "analyze_product_content", category=category, limit=limit, max_score=max_score
        )

    @server.tool(
        name="update_product",
        description=(
            described("update_product")
            + " Called through MCP this writes immediately - confirm with the user first."
        ),
        annotations=DESTRUCTIVE,
    )
    def update_product(
        product_id: int,
        title: str | None = None,
        description: str | None = None,
        price: float | None = None,
        category: str | None = None,
        status: str | None = None,
    ) -> Any:
        return call(
            "update_product",
            product_id=product_id,
            title=title,
            description=description,
            price=price,
            category=category,
            status=status,
        )

    return server


def main() -> None:
    configure_logging(settings.log_level)
    logger.info("MCP server %s starting on stdio with %d tools", SERVER_NAME, len(TOOL_NAMES))
    build_server().run("stdio")


if __name__ == "__main__":
    main()
