import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import httpx
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models import (
    Customer,
    DeliveryPlan,
    DeliveryPlanOrder,
    Driver,
    Location,
    Merchant,
    Order,
    RouteStop,
    Vehicle,
    VehicleDriverAssignment,
    VehicleRoute,
)
from app.db.models.fleet import AssignmentStatus, ResourceStatus
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
    RouteStatus,
    StopStatus,
    StopType,
    ValidationStatus,
)
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.session import engine
from app.main import app


@contextmanager
def operations_client() -> Iterator[tuple[httpx.AsyncClient, Session]]:
    connection = engine.connect()
    outer_transaction = connection.begin()
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
        outer_transaction.rollback()
        connection.close()


def seed_current_operation(session: Session) -> dict[str, UUID | date]:
    suffix = uuid4().hex[:8]
    business_date = date(2026, 10, 3)
    depot = Location(
        id=uuid4(),
        location_code=f"T11-DEPOT-{suffix}",
        display_name="Operations Depot",
        latitude=1.30,
        longitude=103.80,
    )
    pickup = Location(
        id=uuid4(),
        location_code=f"T11-PICKUP-{suffix}",
        display_name="Operations Pickup",
        latitude=1.31,
        longitude=103.81,
    )
    delivery = Location(
        id=uuid4(),
        location_code=f"T11-DELIVERY-{suffix}",
        display_name="Operations Delivery",
        latitude=1.32,
        longitude=103.82,
    )
    merchant = Merchant(
        id=uuid4(),
        merchant_code=f"T11-MER-{suffix}",
        name="Operations Merchant",
        pickup_location=pickup,
        operational_ready_at=datetime(2026, 10, 3, 9, tzinfo=timezone.utc),
        default_pickup_service_seconds=300,
    )
    customer = Customer(
        id=uuid4(),
        customer_code=f"T11-CUS-{suffix}",
        name="Operations Customer",
        default_delivery_location=delivery,
    )
    order = Order(
        id=uuid4(),
        order_code=f"T11-ORD-{suffix}",
        business_date=business_date,
        merchant=merchant,
        customer=customer,
        pickup_location=pickup,
        delivery_location=delivery,
        pickup_ready_at=datetime(2026, 10, 3, 9, tzinfo=timezone.utc),
        pickup_service_seconds=300,
        delivery_window_start_at=datetime(
            2026, 10, 3, 10, tzinfo=timezone.utc
        ),
        delivery_window_end_at=datetime(
            2026, 10, 3, 10, 30, tzinfo=timezone.utc
        ),
        delivery_service_seconds=300,
        demand_load_units=1,
    )
    vehicle = Vehicle(
        id=uuid4(),
        vehicle_code=f"T11-VEH-{suffix}",
        name="Operations Vehicle",
        capacity_load_units=4,
        status=ResourceStatus.AVAILABLE,
        current_location=depot,
        current_location_recorded_at=datetime(
            2026, 10, 3, 8, tzinfo=timezone.utc
        ),
    )
    driver = Driver(
        id=uuid4(),
        driver_code=f"T11-DRV-{suffix}",
        name="Operations Driver",
        status=ResourceStatus.AVAILABLE,
    )
    assignment = VehicleDriverAssignment(
        id=uuid4(),
        vehicle=vehicle,
        driver=driver,
        assigned_from_at=datetime(2026, 10, 3, 8, tzinfo=timezone.utc),
        assigned_until_at=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        status=AssignmentStatus.PLANNED,
    )
    plan = DeliveryPlan(
        id=uuid4(),
        plan_code=f"T11-PLAN-{suffix}",
        plan_group_id=uuid4(),
        business_date=business_date,
        version_no=1,
        status=DeliveryPlanStatus.CURRENT,
        solver_engine="OR_TOOLS",
        validation_status=ValidationStatus.VALID,
        total_distance_meters=10_000,
        total_duration_seconds=5_400,
        vehicle_count=1,
        assigned_order_count=1,
        unassigned_order_count=0,
        validation_summary={"feasible": True},
        activated_at=datetime(2026, 10, 3, 8, tzinfo=timezone.utc),
        created_by="test",
    )
    route = VehicleRoute(
        id=uuid4(),
        delivery_plan=plan,
        route_no=1,
        vehicle=vehicle,
        driver=driver,
        vehicle_driver_assignment=assignment,
        start_location=depot,
        end_location=delivery,
        status=RouteStatus.PLANNED,
        planned_start_at=datetime(2026, 10, 3, 8, 50, tzinfo=timezone.utc),
        planned_end_at=datetime(2026, 10, 3, 10, 25, tzinfo=timezone.utc),
        distance_meters=10_000,
        duration_seconds=5_400,
        vehicle_capacity_load_units_snapshot=4,
        route_metrics={"stop_count": 2},
    )
    pickup_stop = RouteStop(
        id=uuid4(),
        vehicle_route=route,
        order=order,
        location=pickup,
        stop_type=StopType.PICKUP,
        sequence_no=1,
        planned_arrival_at=datetime(2026, 10, 3, 9, tzinfo=timezone.utc),
        planned_departure_at=datetime(
            2026, 10, 3, 9, 5, tzinfo=timezone.utc
        ),
        service_seconds=300,
        time_window_start_at=datetime(
            2026, 10, 3, 9, tzinfo=timezone.utc
        ),
        demand_load_units_snapshot=1,
        status=StopStatus.PLANNED,
    )
    delivery_stop = RouteStop(
        id=uuid4(),
        vehicle_route=route,
        order=order,
        location=delivery,
        stop_type=StopType.DELIVERY,
        sequence_no=2,
        precedence_stop=pickup_stop,
        planned_arrival_at=datetime(
            2026, 10, 3, 10, tzinfo=timezone.utc
        ),
        planned_departure_at=datetime(
            2026, 10, 3, 10, 5, tzinfo=timezone.utc
        ),
        service_seconds=300,
        time_window_start_at=datetime(
            2026, 10, 3, 10, tzinfo=timezone.utc
        ),
        time_window_end_at=datetime(
            2026, 10, 3, 10, 30, tzinfo=timezone.utc
        ),
        demand_load_units_snapshot=1,
        status=StopStatus.PLANNED,
    )
    plan_order = DeliveryPlanOrder(
        delivery_plan=plan,
        order=order,
        assignment_status=PlanOrderAssignmentStatus.ASSIGNED,
        vehicle_route=route,
    )
    session.add_all([plan_order, pickup_stop, delivery_stop])
    session.commit()
    return {
        "business_date": business_date,
        "plan_id": plan.id,
        "route_id": route.id,
        "pickup_stop_id": pickup_stop.id,
        "delivery_stop_id": delivery_stop.id,
        "order_id": order.id,
        "vehicle_id": vehicle.id,
        "driver_id": driver.id,
        "assignment_id": assignment.id,
    }


def test_operations_queries_are_scoped_to_current_plan() -> None:
    with operations_client() as (client, session):
        ids = seed_current_operation(session)
        business_date = ids["business_date"]

        async def scenario() -> None:
            dashboard = await client.get(
                "/api/operations/dashboard",
                params={"business_date": str(business_date)},
            )
            assert dashboard.status_code == 200, dashboard.text
            data = dashboard.json()["data"]
            assert data["current_plan"]["delivery_plan_id"] == str(ids["plan_id"])
            assert data["orders"] == {
                "total": 1,
                "completed": 0,
                "in_progress": 0,
                "at_risk": 0,
            }

            orders = await client.get(
                "/api/operations/orders",
                params={"business_date": str(business_date)},
            )
            assert orders.status_code == 200
            assert orders.json()["data"]["items"][0]["order_id"] == str(
                ids["order_id"]
            )

            vehicles = await client.get(
                "/api/operations/vehicles",
                params={"business_date": str(business_date)},
            )
            assert vehicles.status_code == 200
            assert vehicles.json()["data"]["items"][0]["route_id"] == str(
                ids["route_id"]
            )

            routes = await client.get(
                "/api/operations/routes",
                params={"business_date": str(business_date)},
            )
            assert routes.status_code == 200
            assert routes.json()["data"]["items"][0]["next_stop_id"] == str(
                ids["pickup_stop_id"]
            )

            missing = await client.get(
                "/api/operations/dashboard",
                params={"business_date": "2030-01-01"},
            )
            assert missing.status_code == 404
            assert missing.json()["code"] == "CURRENT_PLAN_NOT_FOUND"

        asyncio.run(scenario())


def test_stop_actions_advance_execution_and_resources_atomically() -> None:
    with operations_client() as (client, session):
        ids = seed_current_operation(session)
        pickup_id = ids["pickup_stop_id"]
        delivery_id = ids["delivery_stop_id"]

        async def action(stop_id: UUID, name: str, occurred_at: str):
            return await client.post(
                f"/api/route-stops/{stop_id}/{name}",
                json={"occurred_at": occurred_at},
            )

        async def scenario() -> None:
            skipped = await action(
                delivery_id, "arrive", "2026-10-03T10:00:00+00:00"
            )
            assert skipped.status_code == 409
            assert skipped.json()["code"] == "INVALID_STOP_STATE_TRANSITION"

            invalid = await action(
                pickup_id, "complete", "2026-10-03T09:05:00+00:00"
            )
            assert invalid.status_code == 409
            assert invalid.json()["code"] == "INVALID_STOP_STATE_TRANSITION"

            arrived = await action(
                pickup_id, "arrive", "2026-10-03T09:00:00+00:00"
            )
            assert arrived.status_code == 200, arrived.text
            assert arrived.json()["data"]["stop"]["status"] == "ARRIVED"
            assert arrived.json()["data"]["order_execution_status"] == (
                "PICKUP_IN_PROGRESS"
            )
            assert arrived.json()["data"]["route_status"] == "ACTIVE"
            assert arrived.json()["data"]["vehicle_status"] == "ACTIVE"
            assert arrived.json()["data"]["driver_status"] == "ACTIVE"

            started = await action(
                pickup_id, "start-service", "2026-10-03T09:01:00+00:00"
            )
            assert started.status_code == 200
            assert started.json()["data"]["stop"]["status"] == "IN_SERVICE"

            pickup_completed = await action(
                pickup_id, "complete", "2026-10-03T09:05:00+00:00"
            )
            assert pickup_completed.status_code == 200
            assert pickup_completed.json()["data"]["order_execution_status"] == (
                "PICKED_UP"
            )

            delivery_arrived = await action(
                delivery_id, "arrive", "2026-10-03T10:20:00+00:00"
            )
            assert delivery_arrived.status_code == 200
            assert delivery_arrived.json()["data"]["order_execution_status"] == (
                "DELIVERING"
            )
            assert delivery_arrived.json()["data"]["order_risk_status"] == (
                "AT_RISK"
            )

            await action(
                delivery_id,
                "start-service",
                "2026-10-03T10:21:00+00:00",
            )
            completed = await action(
                delivery_id, "complete", "2026-10-03T10:25:00+00:00"
            )
            assert completed.status_code == 200
            assert completed.json()["data"]["order_execution_status"] == (
                "COMPLETED"
            )
            assert completed.json()["data"]["order_risk_status"] == "NORMAL"
            assert completed.json()["data"]["route_status"] == "COMPLETED"
            assert completed.json()["data"]["vehicle_status"] == "AVAILABLE"
            assert completed.json()["data"]["driver_status"] == "AVAILABLE"

            repeated = await action(
                delivery_id, "complete", "2026-10-03T10:25:00+00:00"
            )
            assert repeated.status_code == 200

        asyncio.run(scenario())

        session.expire_all()
        order = session.get(Order, ids["order_id"])
        route = session.get(VehicleRoute, ids["route_id"])
        vehicle = session.get(Vehicle, ids["vehicle_id"])
        driver = session.get(Driver, ids["driver_id"])
        assignment = session.get(
            VehicleDriverAssignment, ids["assignment_id"]
        )
        assert order is not None and order.execution_status is OrderExecutionStatus.COMPLETED
        assert order.risk_status is OrderRiskStatus.NORMAL
        assert route is not None and route.status is RouteStatus.COMPLETED
        assert route.actual_start_at is not None and route.actual_end_at is not None
        assert vehicle is not None and vehicle.status is ResourceStatus.AVAILABLE
        assert driver is not None and driver.status is ResourceStatus.AVAILABLE
        assert assignment is not None and assignment.status is AssignmentStatus.ENDED
