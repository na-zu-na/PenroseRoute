"""Database-backed service checks for plan membership and pair conflicts."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessError, Conflict
from app.db.models import Driver, Vehicle
from app.db.models.fleet import ResourceStatus
from app.db.models.resources import OrderExecutionStatus
from app.db.session import engine
from app.modules.resources.assignments import AssignmentService
from app.modules.resources.orders import OrderService


@contextmanager
def service_session():
    connection = engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, autoflush=False, expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def test_planned_order_in_current_plan_is_immutable_and_membership_is_explicit():
    order_id = UUID("40000000-0000-0000-0000-000000000003")
    with service_session() as session:
        service = OrderService(session)
        order = service.get_order(order_id)
        assert order.execution_status == "PLANNED"
        membership, plan = service.current_plan(order_id)
        assert plan.plan_code == "PLAN-20260925-V1"
        assert membership.vehicle_route_id == UUID("90000000-0000-0000-0000-000000000002")
        original_demand = order.demand_load_units
        with pytest.raises(Conflict) as error:
            service.replace_order(order_id, {
                "business_date": order.business_date,
                "merchant_id": order.merchant_id,
                "customer_id": order.customer_id,
                "pickup_location": {}, "delivery_location": {},
                "demand_load_units": original_demand + 1,
            })
        assert error.value.code == "ORDER_NOT_EDITABLE"
        session.refresh(order)
        assert order.demand_load_units == original_demand


def test_execution_transition_preserves_typed_orm_status():
    order_id = UUID("40000000-0000-0000-0000-000000000003")
    with service_session() as session:
        order = OrderService(session).advance_execution(order_id, "PICKUP_IN_PROGRESS")
        assert order.execution_status is OrderExecutionStatus.PICKUP_IN_PROGRESS


def test_assignment_conflicts_are_checked_for_vehicle_and_driver_and_cancel_releases_slot():
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=1)
    end = start + timedelta(hours=2)
    with service_session() as session:
        other_vehicle_id, other_driver_id = uuid4(), uuid4()
        session.add_all([
            Vehicle(
                id=other_vehicle_id, vehicle_code=f"INT-VEH-{uuid4().hex[:8]}",
                name="Integration Van", capacity_load_units=3, status=ResourceStatus.AVAILABLE,
                current_location_id=UUID("10000000-0000-0000-0000-000000000001"),
                current_location_recorded_at=now,
            ),
            Driver(
                id=other_driver_id, driver_code=f"INT-DRV-{uuid4().hex[:8]}",
                name="Integration Driver", status=ResourceStatus.AVAILABLE,
            ),
        ])
        session.commit()
        service = AssignmentService(session)
        vehicle_id = UUID("50000000-0000-0000-0000-000000000003")
        driver_id = UUID("60000000-0000-0000-0000-000000000003")
        common = dict(
            business_date=start.astimezone(ZoneInfo("Asia/Singapore")).date(),
            assignment_start_at=start, assignment_end_at=end,
        )
        first = service.create_assignment(vehicle_id=vehicle_id, driver_id=driver_id, **common)
        for v_id, d_id in ((vehicle_id, other_driver_id), (other_vehicle_id, driver_id)):
            with pytest.raises(Conflict) as error:
                service.create_assignment(vehicle_id=v_id, driver_id=d_id, **common)
            assert error.value.code == "VEHICLE_DRIVER_ASSIGNMENT_CONFLICT"
        adjacent = service.create_assignment(
            vehicle_id=other_vehicle_id, driver_id=driver_id,
            business_date=end.astimezone(ZoneInfo("Asia/Singapore")).date(),
            assignment_start_at=end, assignment_end_at=end + timedelta(hours=1),
        )
        assert adjacent.status == "PLANNED"
        assert service.cancel(first.id).status == "CANCELLED"
        replacement = service.create_assignment(
            vehicle_id=vehicle_id, driver_id=other_driver_id, **common
        )
        assert replacement.status == "PLANNED"


def test_assignment_creation_rejects_unavailable_resources_and_invalid_business_date():
    now = datetime.now(timezone.utc)
    start, end = now + timedelta(hours=1), now + timedelta(hours=2)
    business_date = start.astimezone(ZoneInfo("Asia/Singapore")).date()
    with service_session() as session:
        service = AssignmentService(session)
        with pytest.raises(BusinessError) as error:
            service.create_assignment(
                business_date=business_date + timedelta(days=1),
                vehicle_id=UUID("50000000-0000-0000-0000-000000000003"),
                driver_id=UUID("60000000-0000-0000-0000-000000000003"),
                assignment_start_at=start, assignment_end_at=end,
            )
        assert error.value.code == "VALIDATION_ERROR"
        vehicle_id = UUID("50000000-0000-0000-0000-000000000001")
        driver_id = UUID("60000000-0000-0000-0000-000000000003")
        with pytest.raises(Conflict) as error:
            service.create_assignment(
                business_date=business_date, vehicle_id=vehicle_id, driver_id=driver_id,
                assignment_start_at=start, assignment_end_at=end,
            )
        assert error.value.code == "VEHICLE_NOT_AVAILABLE"
        driver_id = UUID("60000000-0000-0000-0000-000000000001")
        vehicle_id = UUID("50000000-0000-0000-0000-000000000003")
        with pytest.raises(Conflict) as error:
            service.create_assignment(
                business_date=business_date, vehicle_id=vehicle_id, driver_id=driver_id,
                assignment_start_at=start, assignment_end_at=end,
            )
        assert error.value.code == "DRIVER_NOT_AVAILABLE"
