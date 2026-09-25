"""Public P0 Order and Vehicle–Driver Assignment contracts."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.session import engine
from app.main import app


@contextmanager
def api_client():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()


def order_payload(code):
    return {
        "order_code": code,
        "business_date": "2026-10-01",
        "merchant_id": "20000000-0000-0000-0000-000000000001",
        "customer_id": "30000000-0000-0000-0000-000000000001",
        "pickup_location": {
            "location_code": "LOC-MER-001", "address_text": "Jurong East, Singapore",
            "latitude": 1.3329, "longitude": 103.7436,
        },
        "delivery_location": {
            "location_code": "LOC-CUS-001", "address_text": "Bukit Batok, Singapore",
            "latitude": 1.3496, "longitude": 103.749,
        },
        "pickup_ready_at": "2026-10-01T09:00:00+08:00",
        "pickup_service_seconds": 300,
        "delivery_window_start_at": "2026-10-01T10:00:00+08:00",
        "delivery_window_end_at": "2026-10-01T12:00:00+08:00",
        "delivery_service_seconds": 300,
        "demand_load_units": 2,
    }


def test_order_crud_filters_and_forward_only_execution():
    with api_client() as (client, _):
        payload = order_payload(f"TEST-ORD-{uuid4().hex[:8]}")
        created = client.post("/api/orders", json=payload)
        assert created.status_code == 201, created.text
        order_id = created.json()["data"]["id"]
        assert created.json()["data"]["execution_status"] == "PLANNED"
        assert created.json()["data"]["risk_status"] == "NORMAL"

        listed = client.get("/api/orders", params={"business_date": "2026-10-01", "execution_status": "PLANNED"})
        assert listed.status_code == 200
        assert order_id in {item["id"] for item in listed.json()["data"]["items"]}
        assert client.get(f"/api/orders/{order_id}").json()["data"]["current_plan"] is None

        updated = {key: value for key, value in payload.items() if key != "order_code"}
        updated["demand_load_units"] = 3
        assert client.put(f"/api/orders/{order_id}", json=updated).json()["data"]["demand_load_units"] == 3

        for status in ("PICKUP_IN_PROGRESS", "PICKED_UP", "DELIVERING", "COMPLETED"):
            response = client.patch(f"/api/orders/{order_id}/execution-status", json={"status": status})
            assert response.status_code == 200, response.text
            assert response.json()["data"]["execution_status"] == status
        assert client.patch(f"/api/orders/{order_id}/execution-status", json={"status": "COMPLETED"}).status_code == 200
        back = client.patch(f"/api/orders/{order_id}/execution-status", json={"status": "PICKED_UP"})
        assert back.status_code == 409
        assert back.json()["code"] == "INVALID_ORDER_STATE_TRANSITION"
        edit = client.put(f"/api/orders/{order_id}", json=updated)
        assert edit.status_code == 409 and edit.json()["code"] == "ORDER_NOT_EDITABLE"


def test_order_rejects_unmatched_location_and_status_override():
    with api_client() as (client, _):
        payload = order_payload(f"TEST-ORD-{uuid4().hex[:8]}")
        payload["pickup_location"]["location_code"] = "LOC-CUS-001"
        response = client.post("/api/orders", json=payload)
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"
        payload["pickup_location"]["location_code"] = "LOC-MER-001"
        payload["execution_status"] = "COMPLETED"
        assert client.post("/api/orders", json=payload).status_code == 422


def test_order_detail_reads_current_plan_membership_and_put_cannot_rewrite_it():
    order_id = "40000000-0000-0000-0000-000000000003"
    with api_client() as (client, _):
        detail = client.get(f"/api/orders/{order_id}")
        assert detail.status_code == 200
        current = detail.json()["data"]["current_plan"]
        assert current["plan_code"] == "PLAN-20260925-V1"
        assert current["vehicle_route_id"] == "90000000-0000-0000-0000-000000000002"
        assert current["vehicle_id"] == "50000000-0000-0000-0000-000000000002"
        payload = order_payload("IGNORED")
        payload.pop("order_code")
        replacement = client.put(f"/api/orders/{order_id}", json=payload)
        assert replacement.status_code == 409
        assert replacement.json()["code"] == "ORDER_NOT_EDITABLE"


def test_assignment_create_query_conflicts_and_actions():
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(minutes=30), now + timedelta(hours=2)
    payload = {
        "business_date": start.astimezone(ZoneInfo("Asia/Singapore")).date().isoformat(),
        "vehicle_id": "50000000-0000-0000-0000-000000000003",
        "driver_id": "60000000-0000-0000-0000-000000000003",
        "assignment_start_at": start.isoformat(),
        "assignment_end_at": end.isoformat(),
    }
    with api_client() as (client, _):
        created = client.post("/api/vehicle-driver-assignments", json=payload)
        assert created.status_code == 201, created.text
        assignment_id = created.json()["data"]["id"]
        assert created.json()["data"]["status"] == "PLANNED"
        listed = client.get("/api/vehicle-driver-assignments", params={"vehicle_id": payload["vehicle_id"]})
        assert listed.status_code == 200
        assert assignment_id in {item["id"] for item in listed.json()["data"]["items"]}
        assert client.get(f"/api/vehicle-driver-assignments/{assignment_id}").status_code == 200
        conflict = client.post("/api/vehicle-driver-assignments", json=payload)
        assert conflict.status_code == 409 and conflict.json()["code"] == "VEHICLE_DRIVER_ASSIGNMENT_CONFLICT"
        active = client.post(f"/api/vehicle-driver-assignments/{assignment_id}/activate", json={})
        assert active.status_code == 200, active.text
        assert active.json()["data"]["status"] == "ACTIVE"
        cannot_cancel = client.post(f"/api/vehicle-driver-assignments/{assignment_id}/cancel", json={})
        assert cannot_cancel.status_code == 409
        ended = client.post(f"/api/vehicle-driver-assignments/{assignment_id}/end", json={})
        assert ended.status_code == 200, ended.text
        assert ended.json()["data"]["status"] == "ENDED"


def test_assignment_cancel_and_time_validation():
    now = datetime.now(timezone.utc)
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    payload = {
        "business_date": start.astimezone(ZoneInfo("Asia/Singapore")).date().isoformat(),
        "vehicle_id": "50000000-0000-0000-0000-000000000003",
        "driver_id": "60000000-0000-0000-0000-000000000003",
        "assignment_start_at": start.isoformat(),
        "assignment_end_at": end.isoformat(),
    }
    with api_client() as (client, _):
        invalid = {**payload, "assignment_end_at": start.isoformat()}
        response = client.post("/api/vehicle-driver-assignments", json=invalid)
        assert response.status_code == 422
        created = client.post("/api/vehicle-driver-assignments", json=payload)
        assert created.status_code == 201, created.text
        assignment_id = created.json()["data"]["id"]
        listed = client.get("/api/vehicle-driver-assignments", params={
            "business_date": payload["business_date"], "status": "PLANNED",
            "driver_id": payload["driver_id"],
        })
        assert assignment_id in {item["id"] for item in listed.json()["data"]["items"]}
        cancelled = client.post(f"/api/vehicle-driver-assignments/{assignment_id}/cancel", json={})
        assert cancelled.status_code == 200
        assert cancelled.json()["data"]["status"] == "CANCELLED"
        repeat = client.post(f"/api/vehicle-driver-assignments/{assignment_id}/activate", json={})
        assert repeat.status_code == 409
        assert repeat.json()["code"] == "INVALID_ASSIGNMENT_STATE_TRANSITION"
