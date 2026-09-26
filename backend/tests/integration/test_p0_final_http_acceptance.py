"""Final P0 HTTP contract checks with real PostgreSQL and OR-Tools."""

import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Order
from app.db.session import engine
from tests.integration.test_p0_non_agent_e2e import demo_client


def _data(response, expected_status=200, expected_code="SUCCESS"):
    assert response.status_code == expected_status, response.text
    body = response.json()
    assert body["success"] is True
    assert body["code"] == expected_code
    assert body["request_id"]
    return body["data"]


def test_p0_openapi_has_all_59_formal_endpoints_and_no_temporary_recovery():
    resources = {
        "orders": ("GET", "POST"),
        "merchants": ("GET", "POST"),
        "customers": ("GET", "POST"),
        "vehicles": ("GET", "POST"),
        "drivers": ("GET", "POST"),
        "vehicle-driver-assignments": ("GET", "POST"),
    }
    expected = {(method, f"/api/{name}") for name, methods in resources.items() for method in methods}
    expected |= {
        (method, f"/api/{name}/{{}}")
        for name in resources for method in (("GET", "PUT") if name != "vehicle-driver-assignments" else ("GET",))
    }
    expected |= {
        ("PATCH", "/api/orders/{}/execution-status"),
        ("PATCH", "/api/merchants/{}/status"),
        ("PATCH", "/api/merchants/{}/ready-time"),
        ("PATCH", "/api/vehicles/{}/status"),
        ("PATCH", "/api/vehicles/{}/location"),
        ("PATCH", "/api/drivers/{}/status"),
        *(("POST", f"/api/vehicle-driver-assignments/{{}}/{action}") for action in ("activate", "end", "cancel")),
        ("POST", "/api/planning/generate"),
        ("GET", "/api/delivery-plans"),
        ("GET", "/api/delivery-plans/current"),
        ("GET", "/api/delivery-plans/{}"),
        ("GET", "/api/delivery-plans/{}/orders"),
        ("GET", "/api/delivery-plans/{}/routes"),
        ("GET", "/api/vehicle-routes/{}"),
        ("GET", "/api/vehicle-routes/{}/stops"),
        ("GET", "/api/route-stops/{}"),
        *(("POST", f"/api/route-stops/{{}}/{action}") for action in ("arrive", "start-service", "complete")),
        *(("GET", f"/api/operations/{name}") for name in ("dashboard", "orders", "vehicles", "routes")),
        ("POST", "/api/incidents/vehicle-unavailable"),
        ("POST", "/api/incidents/merchant-delay"),
        ("GET", "/api/incidents"),
        ("GET", "/api/incidents/{}"),
        ("GET", "/api/incidents/{}/affected-orders"),
        ("POST", "/api/incidents/{}/recovery"),
        ("GET", "/api/incidents/{}/recovery-plans"),
        ("GET", "/api/recovery-plans/{}"),
        *(("POST", f"/api/recovery-plans/{{}}/{action}") for action in ("approve", "reject", "modify")),
    }
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
    import re
    actual = {
        (method.upper(), re.sub(r"\{[^}]+\}", "{}", path))
        for path, operations in paths.items() for method in operations
        if path.startswith("/api/") and path != "/api/health" and method.lower() in {"get", "post", "put", "patch"}
    }
    # Demo and P1 endpoints do not change the original 59-endpoint P0 contract.
    non_p0 = {
        ("POST", "/api/agent/dispatch"),
        ("GET", "/api/operations/simulated-positions"),
        ("GET", "/api/recovery-plans/{}/comparison"),
    }
    formal_actual = actual - non_p0
    assert expected == formal_actual, f"missing={sorted(expected - formal_actual)}; extra={sorted(formal_actual - expected)}"
    assert len(formal_actual) == 59
    assert not any("deterministic-recovery" in path for path in paths)


def test_p0_starts_without_agent_configuration():
    environment = {
        key: value for key, value in os.environ.items()
        if not any(part in key.upper() for part in ("AGENT", "LLM", "OPENAI", "BEDROCK", "AWS"))
    }
    backend_root = Path(__file__).resolve().parents[2]
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(backend_root), environment.get("PYTHONPATH"))))
    from app.core.config import get_settings

    environment["DATABASE_URL"] = get_settings().database_url
    program = (
        "from fastapi.testclient import TestClient\n"
        "from app.core.config import get_settings\n"
        "from app.main import app\n"
        "get_settings()\n"
        "with TestClient(app) as client:\n"
        "    response = client.get('/api/health')\n"
        "    assert response.status_code == 200, response.text\n"
    )
    with TemporaryDirectory() as isolated_cwd:
        result = subprocess.run(
            [sys.executable, "-c", program], cwd=isolated_cwd, env=environment,
            capture_output=True, text=True,
        )
    assert result.returncode == 0, result.stderr or result.stdout


def test_http_validation_error_uses_p0_envelope():
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post("/api/planning/generate", json={})
    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["request_id"]


def test_http_resources_to_plan_queries_and_stop_execution():
    business_date = date(2026, 10, 8)
    day = datetime(2026, 10, 8, tzinfo=timezone.utc)
    suffix = uuid4().hex[:8]
    pickup = {"location_code": f"HTTP-PICK-{suffix}", "address_text": "Pickup", "latitude": 1.301, "longitude": 103.801}
    delivery = {"location_code": f"HTTP-DROP-{suffix}", "address_text": "Delivery", "latitude": 1.302, "longitude": 103.802}
    hub = {"location_code": f"HTTP-HUB-{suffix}", "address_text": "Hub", "latitude": 1.300, "longitude": 103.800}

    with demo_client(lambda: day + timedelta(hours=9)) as (client, _):
        merchant = _data(client.post("/api/merchants", json={
            "merchant_code": f"HTTP-M-{suffix}", "name": "HTTP Merchant", "pickup_location": pickup,
            "operational_ready_at": (day + timedelta(hours=8)).isoformat(),
            "default_pickup_service_seconds": 60,
        }), 201)
        customer = _data(client.post("/api/customers", json={
            "customer_code": f"HTTP-C-{suffix}", "name": "HTTP Customer", "default_delivery_location": delivery,
        }), 201)
        vehicle = _data(client.post("/api/vehicles", json={
            "vehicle_code": f"HTTP-V-{suffix}", "name": "HTTP Van", "capacity_load_units": 3,
            "current_location": hub, "current_location_recorded_at": (day + timedelta(hours=8)).isoformat(),
        }), 201)
        driver = _data(client.post("/api/drivers", json={
            "driver_code": f"HTTP-DR-{suffix}", "name": "HTTP Driver",
        }), 201)
        assignment = _data(client.post("/api/vehicle-driver-assignments", json={
            "business_date": business_date.isoformat(), "vehicle_id": vehicle["id"], "driver_id": driver["id"],
            "assignment_start_at": (day + timedelta(hours=8)).isoformat(),
            "assignment_end_at": (day + timedelta(hours=14)).isoformat(),
        }), 201)
        order = _data(client.post("/api/orders", json={
            "order_code": f"HTTP-O-{suffix}", "business_date": business_date.isoformat(),
            "merchant_id": merchant["id"], "customer_id": customer["id"],
            "pickup_location": pickup, "delivery_location": delivery,
            "pickup_ready_at": (day + timedelta(hours=8)).isoformat(), "pickup_service_seconds": 60,
            "delivery_window_start_at": (day + timedelta(hours=8, minutes=15)).isoformat(),
            "delivery_window_end_at": (day + timedelta(hours=9)).isoformat(),
            "delivery_service_seconds": 60, "demand_load_units": 1,
        }), 201)
        for resource, identifier in (("merchants", merchant["id"]), ("customers", customer["id"]),
                                     ("vehicles", vehicle["id"]), ("drivers", driver["id"]),
                                     ("orders", order["id"]), ("vehicle-driver-assignments", assignment["id"])):
            assert _data(client.get(f"/api/{resource}/{identifier}"))["id"] == identifier

        generated = _data(
            client.post("/api/planning/generate", json={"business_date": business_date.isoformat()}),
            201, "PLAN_CREATED",
        )
        plan_id = generated["delivery_plan_id"]
        assert generated["status"] == "CURRENT"
        assert generated["summary"]["assigned_orders"] == 1
        assert _data(client.get("/api/delivery-plans/current", params={"business_date": business_date.isoformat()}))["id"] == plan_id
        assert _data(client.get(f"/api/delivery-plans/{plan_id}"))["status"] == "CURRENT"
        memberships = _data(client.get(f"/api/delivery-plans/{plan_id}/orders"))["items"]
        assert [(item["order_id"], item["assignment_status"]) for item in memberships] == [(order["id"], "ASSIGNED")]
        routes = _data(client.get(f"/api/delivery-plans/{plan_id}/routes"))
        assert len(routes) == 1 and routes[0]["vehicle_id"] == vehicle["id"]
        route_id = routes[0]["id"]
        assert _data(client.get(f"/api/vehicle-routes/{route_id}"))["driver_id"] == driver["id"]
        stops = _data(client.get(f"/api/vehicle-routes/{route_id}/stops"))
        assert [stop["stop_type"] for stop in stops] == ["PICKUP", "DELIVERY"]
        assert [stop["sequence_no"] for stop in stops] == [1, 2]

        previous = day + timedelta(hours=8)
        for stop in stops:
            arrived = max(previous + timedelta(seconds=1), datetime.fromisoformat(stop["planned_arrival_at"]))
            for action, event in (("arrive", arrived), ("start-service", arrived + timedelta(seconds=1)),
                                  ("complete", arrived + timedelta(seconds=61))):
                _data(client.post(f"/api/route-stops/{stop['id']}/{action}", json={"occurred_at": event.isoformat()}))
            assert _data(client.get(f"/api/route-stops/{stop['id']}"))["status"] == "COMPLETED"
            previous = arrived + timedelta(seconds=61)
        assert _data(client.get(f"/api/orders/{order['id']}"))["execution_status"] == "COMPLETED"
        assert _data(client.get(f"/api/vehicle-routes/{route_id}"))["status"] == "COMPLETED"

    with Session(engine) as session:
        assert session.scalar(select(Order.id).where(Order.order_code == f"HTTP-O-{suffix}")) is None
