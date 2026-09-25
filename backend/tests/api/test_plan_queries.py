import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models.fleet import Vehicle
from app.db.models.resources import Order
from app.db.session import engine
from app.main import app


CURRENT_PLAN_ID = "80000000-0000-0000-0000-000000000001"
CURRENT_ROUTE_ID = "90000000-0000-0000-0000-000000000001"
UNASSIGNED_ORDER_ID = "40000000-0000-0000-0000-000000000006"
HISTORICAL_ROUTE_ID = "90000000-0000-0000-0000-000000000012"
HISTORICAL_STOP_ID = "c0000000-0000-0000-0000-000000000013"


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
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )
    try:
        yield client, session
    finally:
        asyncio.run(client.aclose())
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()


def test_delivery_plan_list_current_and_detail() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        listed = await client.get(
            "/api/delivery-plans",
            params={"business_date": "2026-09-25", "status": "CURRENT"},
        )
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 1
        assert listed.json()["data"]["items"][0]["id"] == CURRENT_PLAN_ID

        current = await client.get(
            "/api/delivery-plans/current",
            params={"business_date": "2026-09-25"},
        )
        assert current.status_code == 200
        assert current.json()["data"]["id"] == CURRENT_PLAN_ID

        detail = await client.get(f"/api/delivery-plans/{CURRENT_PLAN_ID}")
        assert detail.status_code == 200
        assert detail.json()["data"]["validation_summary"]["feasible"] is True
        assert "routes" not in detail.json()["data"]

    with api_client() as (client, _):
        asyncio.run(scenario(client))


def test_plan_orders_use_membership_table_and_routes_are_version_scoped() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        orders = await client.get(f"/api/delivery-plans/{CURRENT_PLAN_ID}/orders")
        assert orders.status_code == 200
        membership = {item["order_id"]: item for item in orders.json()["data"]["items"]}
        assert UNASSIGNED_ORDER_ID in membership
        assert membership[UNASSIGNED_ORDER_ID]["assignment_status"] == "UNASSIGNED"
        assert membership[UNASSIGNED_ORDER_ID]["vehicle_route_id"] is None

        unassigned = await client.get(
            f"/api/delivery-plans/{CURRENT_PLAN_ID}/orders",
            params={"assignment_status": "UNASSIGNED"},
        )
        assert unassigned.json()["data"]["total"] == 1
        assert unassigned.json()["data"]["items"][0]["order_id"] == UNASSIGNED_ORDER_ID

        routes = await client.get(f"/api/delivery-plans/{CURRENT_PLAN_ID}/routes")
        assert routes.status_code == 200
        assert [route["route_no"] for route in routes.json()["data"]] == [1, 2]
        assert {route["delivery_plan_id"] for route in routes.json()["data"]} == {
            CURRENT_PLAN_ID
        }

    with api_client() as (client, _):
        asyncio.run(scenario(client))


def test_route_and_stop_queries_preserve_snapshots_and_stop_order() -> None:
    async def scenario(client: httpx.AsyncClient, session: Session) -> None:
        vehicle = session.get(Vehicle, "50000000-0000-0000-0000-000000000003")
        order = session.get(Order, "40000000-0000-0000-0000-000000000002")
        assert vehicle is not None and order is not None
        vehicle.capacity_load_units = 99
        order.delivery_service_seconds = 999
        session.flush()

        route = await client.get(f"/api/vehicle-routes/{HISTORICAL_ROUTE_ID}")
        assert route.status_code == 200
        assert route.json()["data"]["vehicle_capacity_load_units_snapshot"] == 4

        stops = await client.get(f"/api/vehicle-routes/{CURRENT_ROUTE_ID}/stops")
        assert stops.status_code == 200
        sequences = [stop["sequence_no"] for stop in stops.json()["data"]]
        assert sequences == sorted(sequences)

        stop = await client.get(f"/api/route-stops/{HISTORICAL_STOP_ID}")
        assert stop.status_code == 200
        assert stop.json()["data"]["stop_type"] == "HANDOVER"
        assert stop.json()["data"]["service_seconds"] == 300
        assert stop.json()["data"]["demand_load_units_snapshot"] == 1

    with api_client() as (client, session):
        asyncio.run(scenario(client, session))


def test_plan_route_and_stop_not_found_errors() -> None:
    missing = "ffffffff-ffff-ffff-ffff-ffffffffffff"

    async def scenario(client: httpx.AsyncClient) -> None:
        cases = (
            (f"/api/delivery-plans/{missing}", "DELIVERY_PLAN_NOT_FOUND"),
            (f"/api/vehicle-routes/{missing}", "VEHICLE_ROUTE_NOT_FOUND"),
            (f"/api/route-stops/{missing}", "ROUTE_STOP_NOT_FOUND"),
        )
        for path, code in cases:
            response = await client.get(path)
            assert response.status_code == 404
            assert response.json()["code"] == code

    with api_client() as (client, _):
        asyncio.run(scenario(client))
