from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import event, select

from app.db.models import Location
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.recovery_repository import RecoveryRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.db.session import SessionLocal, engine


BUSINESS_DATE = date(2026, 9, 25)
CURRENT_PLAN_ID = UUID("80000000-0000-0000-0000-000000000001")
VEHICLE_ID = UUID("50000000-0000-0000-0000-000000000001")
DRIVER_ID = UUID("60000000-0000-0000-0000-000000000001")
MERCHANT_ID = UUID("20000000-0000-0000-0000-000000000002")
ROUTE_ID = UUID("90000000-0000-0000-0000-000000000001")
VEHICLE_INCIDENT_ID = UUID("a0000000-0000-0000-0000-000000000001")
MERCHANT_INCIDENT_ID = UUID("a0000000-0000-0000-0000-000000000002")
RECOVERY_PLAN_ID = UUID("e0000000-0000-0000-0000-000000000001")


def test_resource_repository_reads_orders_and_merchant() -> None:
    with SessionLocal() as session:
        repository = ResourceRepository(session)

        orders = repository.get_orders_for_business_date(BUSINESS_DATE)
        merchant = repository.get_merchant_by_id(MERCHANT_ID)

        assert [order.order_code for order in orders] == [
            "ORD-20260925-001",
            "ORD-20260925-002",
            "ORD-20260925-003",
            "ORD-20260925-004",
            "ORD-20260925-005",
            "ORD-20260925-006",
        ]
        assert all(order.merchant is not None for order in orders)
        assert all(order.customer is not None for order in orders)
        assert merchant is not None
        assert merchant.merchant_code == "MER-002"


def test_fleet_repository_reads_resources_and_time_valid_pairs() -> None:
    at = datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc)

    with SessionLocal() as session:
        repository = FleetRepository(session)

        vehicle = repository.get_vehicle_by_id(VEHICLE_ID)
        driver = repository.get_driver_by_id(DRIVER_ID)
        pairs = repository.get_active_vehicle_driver_pairs(at)

        assert vehicle is not None
        assert vehicle.vehicle_code == "VEH-001"
        assert driver is not None
        assert driver.driver_code == "DRV-001"
        assert [pair.vehicle.vehicle_code for pair in pairs] == [
            "VEH-001",
            "VEH-002",
            "VEH-003",
        ]
        assert all(pair.driver is not None for pair in pairs)


def test_plan_repository_reads_current_plan_routes_and_stops() -> None:
    with SessionLocal() as session:
        repository = PlanRepository(session)

        current_plan = repository.get_current_plan(BUSINESS_DATE)
        plan = repository.get_plan_with_routes(CURRENT_PLAN_ID)
        route = repository.get_route_with_stops(ROUTE_ID)

        assert current_plan is not None
        assert current_plan.plan_code == "PLAN-20260925-V1"
        assert plan is not None
        assert [item.route_no for item in plan.routes] == [1, 2]
        assert len(plan.plan_orders) == 6
        assert route is not None
        assert [stop.sequence_no for stop in route.stops] == [1, 2, 3, 4]


def test_incident_repository_reads_open_incidents_and_impacts() -> None:
    with SessionLocal() as session:
        repository = IncidentRepository(session)

        vehicle_incident = repository.get_open_vehicle_incident(
            CURRENT_PLAN_ID, VEHICLE_ID
        )
        merchant_incident = repository.get_open_merchant_incident(
            CURRENT_PLAN_ID, MERCHANT_ID
        )
        vehicle_impacts = repository.get_incident_affected_orders(
            VEHICLE_INCIDENT_ID
        )

        assert vehicle_incident is not None
        assert vehicle_incident.incident_code == "INC-20260925-VEH-001"
        assert merchant_incident is not None
        assert merchant_incident.incident_code == "INC-20260925-MER-001"
        assert [impact.order.order_code for impact in vehicle_impacts] == [
            "ORD-20260925-001",
            "ORD-20260925-002",
        ]


def test_recovery_repository_reads_attempts_and_uses_for_update() -> None:
    statements: list[str] = []

    def capture_statement(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        with SessionLocal() as session:
            repository = RecoveryRepository(session)
            attempts = repository.get_recovery_attempts(VEHICLE_INCIDENT_ID)
            locked = repository.lock_recovery_plan_for_decision(RECOVERY_PLAN_ID)

            assert [attempt.attempt_no for attempt in attempts] == [1]
            assert locked is not None
            assert locked.recovery_code == "REC-20260925-001"
            assert any("FOR UPDATE" in statement.upper() for statement in statements)
            session.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)


def test_add_and_flush_leave_transaction_ownership_to_caller() -> None:
    code = "LOC-REPOSITORY-ROLLBACK"

    with SessionLocal() as session:
        repository = ResourceRepository(session)
        location = Location(
            location_code=code,
            display_name="Repository Rollback Probe",
            latitude=1.300000,
            longitude=103.800000,
        )

        repository.add_location(location)
        repository.flush()

        assert location.id is not None
        session.rollback()

    with SessionLocal() as verification_session:
        assert verification_session.scalar(
            select(Location).where(Location.location_code == code)
        ) is None

