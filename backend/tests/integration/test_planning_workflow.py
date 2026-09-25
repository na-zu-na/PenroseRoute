import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    ValidationStatus,
)
from app.db.session import engine
from app.main import app


@contextmanager
def planning_client() -> Iterator[tuple[httpx.AsyncClient, Session]]:
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


def seed_planning_facts(
    session: Session,
    *,
    assignment_start: datetime | None = None,
) -> date:
    suffix = uuid4().hex[:8]
    business_date = date(2026, 10, 1)
    start = datetime(2026, 10, 1, 8, tzinfo=timezone.utc)

    depot = Location(
        location_code=f"T9-DEPOT-{suffix}",
        display_name="Task 9 Depot",
        latitude=1.300000,
        longitude=103.800000,
    )
    pickup = Location(
        location_code=f"T9-PICKUP-{suffix}",
        display_name="Task 9 Merchant",
        latitude=1.301000,
        longitude=103.801000,
    )
    delivery_one = Location(
        location_code=f"T9-DELIVERY-1-{suffix}",
        display_name="Task 9 Customer One",
        latitude=1.302000,
        longitude=103.802000,
    )
    delivery_two = Location(
        location_code=f"T9-DELIVERY-2-{suffix}",
        display_name="Task 9 Customer Two",
        latitude=1.303000,
        longitude=103.803000,
    )
    merchant = Merchant(
        merchant_code=f"T9-MER-{suffix}",
        name="Task 9 Merchant",
        pickup_location=pickup,
        operational_ready_at=start + timedelta(minutes=30),
        default_pickup_service_seconds=120,
    )
    customer_one = Customer(
        customer_code=f"T9-CUS-1-{suffix}",
        name="Task 9 Customer One",
        default_delivery_location=delivery_one,
    )
    customer_two = Customer(
        customer_code=f"T9-CUS-2-{suffix}",
        name="Task 9 Customer Two",
        default_delivery_location=delivery_two,
    )
    orders = [
        Order(
            order_code=f"T9-ORD-ASSIGNED-{suffix}",
            business_date=business_date,
            merchant=merchant,
            customer=customer_one,
            pickup_location=pickup,
            delivery_location=delivery_one,
            pickup_ready_at=start + timedelta(minutes=30),
            pickup_service_seconds=120,
            delivery_window_start_at=start + timedelta(minutes=35),
            delivery_window_end_at=start + timedelta(hours=3),
            delivery_service_seconds=120,
            demand_load_units=1,
        ),
        Order(
            order_code=f"T9-ORD-UNASSIGNED-{suffix}",
            business_date=business_date,
            merchant=merchant,
            customer=customer_two,
            pickup_location=pickup,
            delivery_location=delivery_two,
            pickup_ready_at=start + timedelta(minutes=30),
            pickup_service_seconds=120,
            delivery_window_start_at=start + timedelta(minutes=35),
            delivery_window_end_at=start + timedelta(hours=3),
            delivery_service_seconds=120,
            demand_load_units=99,
        ),
    ]
    vehicle = Vehicle(
        vehicle_code=f"T9-VEH-{suffix}",
        name="Task 9 Vehicle",
        capacity_load_units=4,
        status=ResourceStatus.AVAILABLE,
        current_location=depot,
        current_location_recorded_at=start,
    )
    driver = Driver(
        driver_code=f"T9-DRV-{suffix}",
        name="Task 9 Driver",
        status=ResourceStatus.AVAILABLE,
    )
    assignment = VehicleDriverAssignment(
        vehicle=vehicle,
        driver=driver,
        assigned_from_at=assignment_start or start,
        assigned_until_at=start + timedelta(hours=10),
        status=AssignmentStatus.PLANNED,
    )
    session.add_all([*orders, assignment])
    session.commit()
    return business_date


def test_generate_plan_includes_pair_starting_after_first_pickup_ready_time() -> None:
    with planning_client() as (client, session):
        business_date = seed_planning_facts(
            session,
            assignment_start=datetime(
                2026, 10, 1, 9, tzinfo=timezone.utc
            ),
        )

        async def scenario() -> None:
            response = await client.post(
                "/api/planning/generate",
                json={"business_date": business_date.isoformat()},
            )

            assert response.status_code == 201, response.text
            assert response.json()["data"]["summary"]["assigned_orders"] == 1

        asyncio.run(scenario())


def test_generate_plan_does_not_mask_unrelated_integrity_error(
    monkeypatch,
) -> None:
    from app.modules.planning import workflow as planning_workflow

    class DatabaseFailure(Exception):
        diag = type("Diag", (), {"constraint_name": "fk_unrelated"})()

    with planning_client() as (client, session):
        business_date = seed_planning_facts(session)

        def fail_finalization(*args, **kwargs):
            raise IntegrityError("insert", {}, DatabaseFailure())

        monkeypatch.setattr(
            planning_workflow.PlanFinalizer,
            "finalize",
            fail_finalization,
        )

        async def scenario() -> None:
            response = await client.post(
                "/api/planning/generate",
                json={"business_date": business_date.isoformat()},
            )

            assert response.status_code == 500
            assert response.json()["code"] == "INTERNAL_ERROR"

        asyncio.run(scenario())


def test_generate_plan_persists_complete_current_plan_without_long_transaction(
    monkeypatch,
) -> None:
    from app.modules.planning import workflow as planning_workflow

    with planning_client() as (client, session):
        business_date = seed_planning_facts(session)
        original_matrix_builder = planning_workflow.build_distance_time_matrix
        original_solve = planning_workflow.ORToolsSolver.solve

        def checked_matrix_builder(*args, **kwargs):
            assert not session.in_transaction()
            return original_matrix_builder(*args, **kwargs)

        def checked_solve(solver, solver_input):
            assert not session.in_transaction()
            return original_solve(solver, solver_input)

        monkeypatch.setattr(
            planning_workflow,
            "build_distance_time_matrix",
            checked_matrix_builder,
        )
        monkeypatch.setattr(
            planning_workflow.ORToolsSolver,
            "solve",
            checked_solve,
        )

        async def scenario() -> None:
            response = await client.post(
                "/api/planning/generate",
                json={"business_date": business_date.isoformat()},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["code"] == "PLAN_CREATED"
            assert body["data"]["status"] == "CURRENT"
            summary = body["data"]["summary"]
            assert summary["total_orders"] == 2
            assert summary["assigned_orders"] == 1
            assert summary["unassigned_orders"] == 1
            assert summary["vehicle_count"] == 1
            assert summary["total_distance_meters"] > 0
            assert summary["total_duration_seconds"] > 0
            assert body["data"]["unassigned_orders"][0][
                "unassigned_reason_code"
            ] == "CAPACITY_INFEASIBLE"

            plan_id = body["data"]["delivery_plan_id"]
            session.expire_all()
            plan = session.scalar(
                select(DeliveryPlan).where(DeliveryPlan.id == plan_id)
            )
            assert plan is not None
            assert plan.status is DeliveryPlanStatus.CURRENT
            assert plan.validation_status is ValidationStatus.VALID
            assert session.scalar(
                select(func.count())
                .select_from(DeliveryPlanOrder)
                .where(DeliveryPlanOrder.delivery_plan_id == plan.id)
            ) == 2
            statuses = set(
                session.scalars(
                    select(DeliveryPlanOrder.assignment_status).where(
                        DeliveryPlanOrder.delivery_plan_id == plan.id
                    )
                )
            )
            assert statuses == {
                PlanOrderAssignmentStatus.ASSIGNED,
                PlanOrderAssignmentStatus.UNASSIGNED,
            }
            assert session.scalar(
                select(func.count())
                .select_from(VehicleRoute)
                .where(VehicleRoute.delivery_plan_id == plan.id)
            ) == 1
            assert session.scalar(
                select(func.count())
                .select_from(RouteStop)
                .join(VehicleRoute)
                .where(VehicleRoute.delivery_plan_id == plan.id)
            ) == 2

            repeated = await client.post(
                "/api/planning/generate",
                json={"business_date": business_date.isoformat()},
            )
            assert repeated.status_code == 409
            assert repeated.json()["code"] == "CURRENT_PLAN_ALREADY_EXISTS"
            assert session.scalar(
                select(func.count())
                .select_from(DeliveryPlan)
                .where(
                    DeliveryPlan.business_date == business_date,
                    DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
                )
            ) == 1

        asyncio.run(scenario())
