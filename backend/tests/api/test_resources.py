import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models.fleet import ResourceStatus, Vehicle
from app.db.session import engine
from app.main import app


@contextmanager
def api_client() -> Iterator[tuple[httpx.AsyncClient, Session]]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    def override_get_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    try:
        yield client, session
    finally:
        asyncio.run(client.aclose())
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def location_payload(code: str, latitude: float, longitude: float) -> dict[str, object]:
    return {
        "location_code": code,
        "address_text": f"{code} address",
        "latitude": latitude,
        "longitude": longitude,
    }


def test_merchant_resource_endpoints_and_ready_time_boundary() -> None:
    suffix = uuid4().hex[:8]
    merchant_code = f"T-MER-{suffix}"
    location_code = f"T-LOC-MER-{suffix}"

    async def scenario(client: httpx.AsyncClient) -> None:
        created = await client.post(
            "/api/merchants",
            json={
                "merchant_code": merchant_code,
                "name": "Test Merchant",
                "pickup_location": location_payload(location_code, 1.31, 103.71),
                "operational_ready_at": "2026-09-25T10:00:00+08:00",
                "default_pickup_service_seconds": 300,
            },
        )
        assert created.status_code == 201
        merchant_id = created.json()["data"]["id"]

        listed = await client.get("/api/merchants", params={"preparation_status": "PREPARING"})
        assert listed.status_code == 200
        assert merchant_id in {item["id"] for item in listed.json()["data"]["items"]}

        detailed = await client.get(f"/api/merchants/{merchant_id}")
        assert detailed.json()["data"]["pickup_location"]["location_code"] == location_code

        replaced = await client.put(
            f"/api/merchants/{merchant_id}",
            json={
                "name": "Updated Merchant",
                "pickup_location": location_payload(location_code, 1.31, 103.71),
                "default_pickup_service_seconds": 420,
            },
        )
        assert replaced.status_code == 200
        assert replaced.json()["data"]["name"] == "Updated Merchant"

        status = await client.patch(
            f"/api/merchants/{merchant_id}/status",
            json={"status": "READY"},
        )
        assert status.status_code == 200
        assert status.json()["data"]["preparation_status"] == "READY"

        regressed_status = await client.patch(
            f"/api/merchants/{merchant_id}/status",
            json={"status": "PREPARING"},
        )
        assert regressed_status.status_code == 409
        assert regressed_status.json()["code"] == "INVALID_MERCHANT_STATUS_TRANSITION"

        delayed_status = await client.patch(
            f"/api/merchants/{merchant_id}/status",
            json={"status": "DELAYED"},
        )
        assert delayed_status.status_code == 422
        assert delayed_status.json()["code"] == "VALIDATION_ERROR"

    with api_client() as (client, _):
        asyncio.run(scenario(client))


def test_customer_resource_endpoints_preserve_location_reference() -> None:
    suffix = uuid4().hex[:8]
    customer_code = f"T-CUS-{suffix}"
    location_code = f"T-LOC-CUS-{suffix}"

    async def scenario(client: httpx.AsyncClient) -> None:
        created = await client.post(
            "/api/customers",
            json={
                "customer_code": customer_code,
                "name": "Test Customer",
                "default_delivery_location": location_payload(location_code, 1.32, 103.72),
            },
        )
        assert created.status_code == 201
        customer_id = created.json()["data"]["id"]

        listed = await client.get("/api/customers")
        assert customer_id in {item["id"] for item in listed.json()["data"]["items"]}

        replaced = await client.put(
            f"/api/customers/{customer_id}",
            json={
                "name": "Updated Customer",
                "default_delivery_location": location_payload(location_code, 1.32, 103.72),
            },
        )
        assert replaced.status_code == 200

        detailed = await client.get(f"/api/customers/{customer_id}")
        assert detailed.json()["data"]["name"] == "Updated Customer"
        assert detailed.json()["data"]["default_delivery_location"]["location_code"] == location_code

    with api_client() as (client, _):
        asyncio.run(scenario(client))


def test_vehicle_resource_endpoints_and_incident_boundary() -> None:
    suffix = uuid4().hex[:8]
    vehicle_code = f"T-VEH-{suffix}"

    async def scenario(client: httpx.AsyncClient, session: Session) -> None:
        created = await client.post(
            "/api/vehicles",
            json={
                "vehicle_code": vehicle_code,
                "name": "Test Vehicle",
                "capacity_load_units": 8,
                "current_location": location_payload(f"T-LOC-VEH-{suffix}", 1.33, 103.73),
                "current_location_recorded_at": "2026-09-25T08:00:00+08:00",
            },
        )
        assert created.status_code == 201
        vehicle_id = created.json()["data"]["id"]

        listed = await client.get("/api/vehicles", params={"status": "AVAILABLE"})
        assert vehicle_id in {item["id"] for item in listed.json()["data"]["items"]}

        unavailable_resource = await client.patch(
            f"/api/vehicles/{vehicle_id}/status",
            json={"status": "UNAVAILABLE", "business_date": "2026-09-25"},
        )
        assert unavailable_resource.status_code == 200
        restored = await client.patch(
            f"/api/vehicles/{vehicle_id}/status",
            json={"status": "AVAILABLE"},
        )
        assert restored.status_code == 200

        replaced = await client.put(
            f"/api/vehicles/{vehicle_id}",
            json={"name": "Updated Vehicle", "capacity_load_units": 9},
        )
        assert replaced.status_code == 200

        moved = await client.patch(
            f"/api/vehicles/{vehicle_id}/location",
            json={
                "location": location_payload(f"T-LOC-VEH-MOVED-{suffix}", 1.34, 103.74),
                "recorded_at": "2026-09-25T08:30:00+08:00",
            },
        )
        assert moved.status_code == 200
        assert moved.json()["data"]["current_location"]["latitude"] == 1.34

        vehicle = session.get(Vehicle, vehicle_id)
        assert vehicle is not None
        vehicle.status = ResourceStatus.ACTIVE
        session.commit()

        missing_business_date = await client.patch(
            f"/api/vehicles/{vehicle_id}/status",
            json={"status": "UNAVAILABLE"},
        )
        assert missing_business_date.status_code == 422
        assert missing_business_date.json()["code"] == "VALIDATION_ERROR"
        session.refresh(vehicle)
        assert vehicle.status == ResourceStatus.ACTIVE

        unavailable = await client.patch(
            f"/api/vehicles/{vehicle_id}/status",
            json={"status": "UNAVAILABLE", "business_date": "2026-09-25"},
        )
        assert unavailable.status_code == 409
        assert unavailable.json()["code"] == "VEHICLE_NOT_EXECUTING_ROUTE"
        session.refresh(vehicle)
        assert vehicle.status == ResourceStatus.ACTIVE

        detailed = await client.get(f"/api/vehicles/{vehicle_id}")
        assert detailed.json()["data"]["name"] == "Updated Vehicle"

    with api_client() as (client, session):
        asyncio.run(scenario(client, session))


def test_available_vehicle_with_active_route_cannot_be_marked_unavailable() -> None:
    vehicle_id = "50000000-0000-0000-0000-000000000002"

    async def scenario(client: httpx.AsyncClient, session: Session) -> None:
        vehicle = session.get(Vehicle, vehicle_id)
        assert vehicle is not None
        vehicle.status = ResourceStatus.AVAILABLE
        session.commit()

        response = await client.patch(
            f"/api/vehicles/{vehicle_id}/status",
            json={"status": "UNAVAILABLE"},
        )

        assert response.status_code == 409
        assert response.json()["code"] == "VEHICLE_NOT_AVAILABLE"
        session.refresh(vehicle)
        assert vehicle.status == ResourceStatus.AVAILABLE

    with api_client() as (client, session):
        asyncio.run(scenario(client, session))


def test_driver_resource_endpoints_report_manual_intervention() -> None:
    suffix = uuid4().hex[:8]
    driver_code = f"T-DRV-{suffix}"

    async def scenario(client: httpx.AsyncClient) -> None:
        created = await client.post(
            "/api/drivers",
            json={"driver_code": driver_code, "name": "Test Driver"},
        )
        assert created.status_code == 201
        driver_id = created.json()["data"]["id"]

        listed = await client.get("/api/drivers", params={"status": "AVAILABLE"})
        assert driver_id in {item["id"] for item in listed.json()["data"]["items"]}

        replaced = await client.put(
            f"/api/drivers/{driver_id}",
            json={"name": "Updated Driver"},
        )
        assert replaced.status_code == 200

        active = await client.patch(
            f"/api/drivers/{driver_id}/status",
            json={"status": "ACTIVE"},
        )
        assert active.status_code == 200

        unavailable = await client.patch(
            f"/api/drivers/{driver_id}/status",
            json={"status": "UNAVAILABLE"},
        )
        assert unavailable.status_code == 200
        assert unavailable.json()["data"]["manual_intervention_required"] is True
        assert unavailable.json()["data"]["driver"]["status"] == "UNAVAILABLE"

        invalid_active = await client.patch(
            f"/api/drivers/{driver_id}/status",
            json={"status": "ACTIVE"},
        )
        assert invalid_active.status_code == 409
        assert invalid_active.json()["code"] == "DRIVER_NOT_AVAILABLE"

        detailed = await client.get(f"/api/drivers/{driver_id}")
        assert detailed.json()["data"]["name"] == "Updated Driver"
        assert detailed.json()["data"]["status"] == "UNAVAILABLE"

    with api_client() as (client, _):
        asyncio.run(scenario(client))
