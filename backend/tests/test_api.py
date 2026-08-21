"""HTTP surface: health, products, agent runs, streaming and approvals."""

from __future__ import annotations

from app.db.models import ApprovalStatus, Product, RunStatus
from tests.fakes import answer, tool_call

NEW_COPY = (
    "The Trailhead 2 is a cushioned road shoe for runners covering 30 to 50 km a week. "
    "Its 28 mm EVA midsole and 250 g weight keep long efforts comfortable, and the mesh "
    "upper dries fast after wet runs. Ideal for daily training and weekend long runs."
)


# --- system -----------------------------------------------------------
def test_health_reports_dependencies(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm"]["provider"] == "fake"
    assert body["llm"]["available"] is True


def test_tools_endpoint_documents_the_registry(client):
    tools = client.get("/tools").json()
    names = {t["name"] for t in tools}
    assert "get_products" in names
    assert "update_product" in names

    update = next(t for t in tools if t["name"] == "update_product")
    assert update["access"] == "write"
    assert update["requires_approval"] is True
    assert update["input_schema"]["properties"]["product_id"]
    assert all(t["requires_approval"] is False for t in tools if t["access"] == "read")


def test_mcp_health_reports_the_real_server(client):
    body = client.get("/mcp/health").json()
    assert body["status"] == "ok", body
    assert body["server_name"] == "ecommerce-ops"
    assert body["tool_count"] == len(body["tools"])
    assert "update_product" in body["tools"]


def test_openapi_document_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert "/agent/run" in schema["paths"]
    assert "/approvals/{approval_id}/approve" in schema["paths"]


# --- products ---------------------------------------------------------
def test_list_products_includes_content_scores(client):
    body = client.get("/products").json()
    assert body["total"] == 4
    assert sorted(body["categories"]) == ["Clothing", "Gym Equipment", "Running Shoes"]
    item = body["items"][0]
    assert 0 <= item["content"]["score"] <= 100
    assert item["content"]["grade"]


def test_product_filters(client):
    assert client.get("/products", params={"category": "Running Shoes"}).json()["total"] == 2
    assert client.get("/products", params={"search": "yoga"}).json()["total"] == 1


def test_get_missing_product_returns_404(client):
    response = client.get("/products/99999")
    assert response.status_code == 404


def test_direct_product_edit_applies_immediately(client, db, product_id):
    response = client.put(f"/products/{product_id}", json={"description": NEW_COPY})
    assert response.status_code == 200
    assert response.json()["content"]["score"] > 40
    db.expire_all()
    assert db.get(Product, product_id).description == NEW_COPY


def test_product_edit_validates_input(client, product_id):
    assert client.put(f"/products/{product_id}", json={"price": -1}).status_code == 422
    assert client.put(f"/products/{product_id}", json={}).status_code == 422


# --- analytics --------------------------------------------------------
def test_analytics_summary(client):
    body = client.get("/analytics/summary").json()
    assert body["product_count"] == 4
    assert body["low_stock_count"] == 2
    assert body["out_of_stock_count"] == 1
    assert body["sales"]["order_count"] == 22
    assert body["top_sellers"][0]["sku"] == "RUN-001"
    assert 0 <= body["average_content_score"] <= 100


# --- agent ------------------------------------------------------------
def test_start_a_run_and_read_it_back(client, llm):
    llm.script(tool_call("get_low_stock_products", limit=5), answer("Two are low."))

    response = client.post("/agent/run", json={"message": "Which products are low on stock?"})
    assert response.status_code == 202
    run = response.json()
    assert run["status"] == RunStatus.COMPLETED.value
    assert run["final_response"] == "Two are low."
    assert run["tools_used"] == ["get_low_stock_products"]

    detail = client.get(f"/agent/runs/{run['id']}").json()
    assert [s["step_type"] for s in detail["steps"]][0] == "request"
    assert detail["duration_ms"] >= 0


def test_run_requires_a_message(client):
    assert client.post("/agent/run", json={"message": ""}).status_code == 422
    assert client.post("/agent/run", json={}).status_code == 422


def test_missing_run_returns_404(client):
    assert client.get("/agent/runs/424242").status_code == 404


def test_runs_can_be_listed_and_filtered(client, llm):
    llm.fallback = answer("Done.")
    client.post("/agent/run", json={"message": "first"})
    client.post("/agent/run", json={"message": "second"})

    body = client.get("/agent/runs", params={"limit": 10}).json()
    assert body["count"] == 2
    assert body["runs"][0]["user_request"] == "second"  # newest first

    completed = client.get("/agent/runs", params={"status": "COMPLETED"}).json()
    assert completed["count"] == 2
    assert client.get("/agent/runs", params={"status": "FAILED"}).json()["count"] == 0


def test_event_stream_replays_the_run(client, llm):
    llm.script(tool_call("get_products", limit=5), answer("Four products."))
    run_id = client.post("/agent/run", json={"message": "list products"}).json()["id"]

    with client.stream("GET", f"/agent/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    assert "event: step" in body
    assert "Calling get_products" in body
    assert "event: done" in body
    assert "Four products." in body


def test_event_stream_404s_for_an_unknown_run(client):
    assert client.get("/agent/runs/999999/events").status_code == 404


# --- approvals --------------------------------------------------------
def _paused_run(client, llm, product_id) -> dict:
    llm.script(
        tool_call("update_product", product_id=product_id, description=NEW_COPY),
        answer("The description was updated."),
    )
    return client.post("/agent/run", json={"message": "Improve RUN-003"}).json()


def test_pending_approval_is_listed(client, llm, product_id, db):
    run = _paused_run(client, llm, product_id)
    assert run["status"] == RunStatus.WAITING_FOR_APPROVAL.value

    approvals = client.get("/approvals").json()
    assert len(approvals) == 1
    assert approvals[0]["tool_name"] == "update_product"
    assert approvals[0]["preview"]["changes"][0]["field"] == "description"
    assert db.get(Product, product_id).description == "Comfortable running shoes."


def test_approve_endpoint_applies_the_change(client, llm, product_id, db):
    run = _paused_run(client, llm, product_id)
    approval_id = client.get("/approvals").json()[0]["id"]

    response = client.post(f"/approvals/{approval_id}/approve", json={"note": "Looks good"})
    assert response.status_code == 200
    assert response.json()["status"] == ApprovalStatus.APPROVED.value

    db.expire_all()
    assert db.get(Product, product_id).description == NEW_COPY
    assert client.get(f"/agent/runs/{run['id']}").json()["status"] == RunStatus.COMPLETED.value
    assert client.get("/approvals").json() == []


def test_reject_endpoint_changes_nothing(client, llm, product_id, db):
    run = _paused_run(client, llm, product_id)
    approval_id = client.get("/approvals").json()[0]["id"]

    response = client.post(f"/approvals/{approval_id}/reject", json={"note": "Wrong tone"})
    assert response.json()["status"] == ApprovalStatus.REJECTED.value
    assert response.json()["decision_note"] == "Wrong tone"

    db.expire_all()
    assert db.get(Product, product_id).description == "Comfortable running shoes."
    assert client.get(f"/agent/runs/{run['id']}").json()["status"] == RunStatus.COMPLETED.value


def test_resolving_twice_conflicts(client, llm, product_id):
    _paused_run(client, llm, product_id)
    approval_id = client.get("/approvals").json()[0]["id"]

    assert client.post(f"/approvals/{approval_id}/approve").status_code == 200
    assert client.post(f"/approvals/{approval_id}/reject").status_code == 409


def test_unknown_approval_returns_404(client):
    assert client.post("/approvals/98765/approve").status_code == 404
    assert client.get("/approvals/98765").status_code == 404


def test_approval_history_is_queryable(client, llm, product_id):
    _paused_run(client, llm, product_id)
    approval_id = client.get("/approvals").json()[0]["id"]
    client.post(f"/approvals/{approval_id}/reject")

    assert client.get("/approvals").json() == []
    history = client.get("/approvals", params={"status": "all"}).json()
    assert len(history) == 1
    assert history[0]["status"] == ApprovalStatus.REJECTED.value
