"""Tool registry and individual tool behaviour."""

from __future__ import annotations

import pytest

from app.db.models import Product
from app.tools import ToolAccess, ToolError, ToolNotFoundError, ToolValidationError
from tests.fakes import answer


# --- registry ---------------------------------------------------------
def test_every_tool_declares_its_contract(registry):
    for tool in registry.list():
        assert tool.description
        assert tool.category
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema
        assert tool.requires_approval == (tool.access is ToolAccess.WRITE)


def test_only_update_product_is_a_write(registry):
    writes = {t.name for t in registry.list(access=ToolAccess.WRITE)}
    assert writes == {"update_product"}


def test_llm_specs_are_function_shaped(registry):
    specs = registry.llm_specs()
    assert len(specs) == len(registry)
    for spec in specs:
        assert spec["type"] == "function"
        assert spec["function"]["name"]
        assert spec["function"]["parameters"]["type"] == "object"


def test_unknown_tool_raises(registry, ctx):
    with pytest.raises(ToolNotFoundError):
        registry.execute("delete_everything", {}, ctx)


def test_invalid_arguments_raise_validation_error(registry, ctx):
    with pytest.raises(ToolValidationError) as exc:
        registry.execute("get_products", {"limit": 5000}, ctx)
    assert "limit" in str(exc.value)


def test_duplicate_registration_is_rejected(registry):
    from app.tools import build_registry
    from app.tools.products import register

    fresh = build_registry()
    with pytest.raises(ValueError, match="Duplicate"):
        register(fresh)


# --- product tools ----------------------------------------------------
def test_get_products_returns_summaries(registry, ctx):
    result = registry.execute("get_products", {"limit": 10}, ctx)
    assert result["count"] == 4
    assert result["total_matching"] == 4
    first = result["products"][0]
    assert {"sku", "title", "price", "inventory_quantity", "description_preview"} <= set(first)


def test_get_products_filters_by_category(registry, ctx):
    result = registry.execute("get_products", {"category": "Running Shoes"}, ctx)
    assert result["count"] == 2
    assert {p["category"] for p in result["products"]} == {"Running Shoes"}


def test_get_product_by_sku_and_id(registry, ctx, product_id):
    by_sku = registry.execute("get_product", {"sku": "RUN-003"}, ctx)
    by_id = registry.execute("get_product", {"product_id": product_id}, ctx)
    assert by_sku["id"] == by_id["id"] == product_id
    assert by_sku["description"] == "Comfortable running shoes."


def test_get_product_requires_an_identifier(registry, ctx):
    with pytest.raises(ToolValidationError):
        registry.execute("get_product", {}, ctx)


def test_missing_product_is_a_tool_error(registry, ctx):
    with pytest.raises(ToolError, match="not found"):
        registry.execute("get_product", {"product_id": 99999}, ctx)


def test_search_matches_title_and_description(registry, ctx):
    assert registry.execute("search_products", {"query": "yoga"}, ctx)["count"] == 1
    assert registry.execute("search_products", {"query": "running"}, ctx)["count"] == 2
    assert registry.execute("search_products", {"query": "zzzz"}, ctx)["count"] == 0


# --- inventory --------------------------------------------------------
def test_low_stock_uses_each_reorder_point(registry, ctx):
    result = registry.execute("get_low_stock_products", {}, ctx)
    skus = {i["sku"] for i in result["items"]}
    assert skus == {"CLO-004", "GYM-003"}  # 0/20 and 4/30
    assert all(i["below_reorder_point"] for i in result["items"])


def test_low_stock_accepts_an_absolute_threshold(registry, ctx):
    result = registry.execute("get_low_stock_products", {"threshold": 1}, ctx)
    assert {i["sku"] for i in result["items"]} == {"CLO-004"}


def test_inventory_is_sorted_lowest_first(registry, ctx):
    items = registry.execute("get_inventory", {}, ctx)["items"]
    quantities = [i["quantity"] for i in items]
    assert quantities == sorted(quantities)


# --- analytics --------------------------------------------------------
def test_sales_summary_totals(registry, ctx):
    result = registry.execute("get_sales_summary", {"days": 30}, ctx)
    assert result["order_count"] == 22
    assert result["total_revenue"] == pytest.approx(139 * 12 + 89 * 6 + 29 * 3 + 59)
    assert result["average_order_value"] > 0
    assert {c["category"] for c in result["by_category"]} == {
        "Running Shoes",
        "Gym Equipment",
        "Clothing",
    }


def test_best_sellers_are_ranked(registry, ctx):
    products = registry.execute("get_best_sellers", {"limit": 3}, ctx)["products"]
    units = [p["units_sold"] for p in products]
    assert units == sorted(units, reverse=True)
    assert products[0]["sku"] == "RUN-001"


def test_product_performance_includes_stock_cover(registry, ctx, product_id):
    result = registry.execute("get_product_performance", {"product_id": product_id}, ctx)
    assert result["units_sold"] == 6
    assert result["days_of_stock_remaining"] > 0
    assert 0 <= result["content_score"] <= 100


# --- content ----------------------------------------------------------
def test_analysis_returns_weakest_first(registry, ctx):
    result = registry.execute("analyze_product_content", {"limit": 4}, ctx)
    scores = [p["score"] for p in result["products"]]
    assert scores == sorted(scores)
    assert result["products"][0]["sku"] in {"CLO-004", "GYM-003"}
    assert result["analyzed"] == 4


def test_analysis_can_filter_by_max_score(registry, ctx):
    result = registry.execute("analyze_product_content", {"max_score": 40}, ctx)
    assert all(p["score"] <= 40 for p in result["products"])
    assert "RUN-001" not in {p["sku"] for p in result["products"]}


def test_generate_description_uses_the_llm(registry, ctx, llm, product_id):
    llm.script(
        answer(
            "The Trailhead 2 is a cushioned road shoe for runners covering 30 to 50 km "
            "a week. Its 28 mm EVA midsole and 250 g weight keep long efforts "
            "comfortable, and the mesh upper dries fast after wet runs. Ideal for "
            "daily training and weekend long runs. Machine washable at 30 degrees."
        )
    )
    result = registry.execute(
        "generate_product_description", {"product_id": product_id}, ctx
    )
    assert result["field"] == "description"
    assert result["proposed_score"] > result["current_score"]
    assert result["current_value"] == "Comfortable running shoes."


def test_generate_description_strips_model_preamble(registry, ctx, llm, product_id):
    llm.script(
        answer(
            'Here is the improved description:\n"A 6 mm closed-cell foam mat measuring '
            "1830 by 610 mm and weighing 1.2 kg. Ideal for daily practice at home. "
            'Wipe clean with a damp cloth and dry flat before rolling."'
        )
    )
    result = registry.execute(
        "generate_product_description", {"product_id": product_id}, ctx
    )
    assert not result["proposed_value"].lower().startswith("here is")
    assert not result["proposed_value"].startswith('"')


def test_generate_description_surfaces_llm_failure(registry, ctx, llm, product_id):
    llm.available = False
    with pytest.raises(ToolError, match="Content generation failed"):
        registry.execute("generate_product_description", {"product_id": product_id}, ctx)


# --- writes -----------------------------------------------------------
def test_update_product_writes_and_reports_the_diff(registry, ctx, db, product_id):
    result = registry.execute(
        "update_product",
        {"product_id": product_id, "description": "A much longer and better description."},
        ctx,
    )
    assert result["updated_fields"] == ["description"]
    assert result["before"]["description"] == "Comfortable running shoes."
    db.expire_all()
    assert db.get(Product, product_id).description.startswith("A much longer")


def test_update_with_no_change_is_an_error(registry, ctx, product_id):
    with pytest.raises(ToolError, match="nothing was changed"):
        registry.execute(
            "update_product",
            {"product_id": product_id, "description": "Comfortable running shoes."},
            ctx,
        )


def test_update_requires_at_least_one_field(registry, ctx, product_id):
    with pytest.raises(ToolValidationError):
        registry.execute("update_product", {"product_id": product_id}, ctx)


def test_update_rejects_a_negative_price(registry, ctx, product_id):
    with pytest.raises(ToolValidationError):
        registry.execute("update_product", {"product_id": product_id, "price": -5}, ctx)
