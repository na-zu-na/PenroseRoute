from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.db.models.fleet import AssignmentStatus, ResourceStatus
from app.db.models.resources import OrderExecutionStatus
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.routing.contracts import RoutingMatrix
from app.modules.planning.input_builder import (
    build_solver_input,
    materialize_planning_facts,
)


def planning_entities(*, assignment_end: datetime | None = None):
    business_date = date(2026, 10, 2)
    window_start = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
    ready_at = window_start + timedelta(hours=1)
    window_end = ready_at
    pickup = SimpleNamespace(
        id=uuid4(), latitude=Decimal("1.30"), longitude=Decimal("103.80")
    )
    delivery = SimpleNamespace(
        id=uuid4(), latitude=Decimal("1.31"), longitude=Decimal("103.81")
    )
    depot = SimpleNamespace(
        id=uuid4(), latitude=Decimal("1.29"), longitude=Decimal("103.79")
    )
    order = SimpleNamespace(
        id=uuid4(),
        order_code="OPEN-WINDOW",
        business_date=business_date,
        merchant_id=uuid4(),
        customer_id=uuid4(),
        pickup_location_id=pickup.id,
        delivery_location_id=delivery.id,
        pickup_location=pickup,
        delivery_location=delivery,
        pickup_ready_at=ready_at,
        pickup_service_seconds=0,
        delivery_window_start_at=window_start,
        delivery_window_end_at=window_end,
        delivery_service_seconds=10,
        demand_load_units=1,
        execution_status=OrderExecutionStatus.PLANNED,
    )
    vehicle = SimpleNamespace(
        id=uuid4(),
        status=ResourceStatus.AVAILABLE,
        capacity_load_units=2,
        current_location_id=depot.id,
        current_location=depot,
    )
    driver = SimpleNamespace(id=uuid4(), status=ResourceStatus.AVAILABLE)
    assignment = SimpleNamespace(
        id=uuid4(),
        vehicle_id=vehicle.id,
        driver_id=driver.id,
        assigned_from_at=ready_at,
        assigned_until_at=assignment_end,
        status=AssignmentStatus.PLANNED,
        vehicle=vehicle,
        driver=driver,
    )
    return business_date, window_start, order, assignment


def test_materialized_time_baseline_includes_an_already_open_delivery_window() -> None:
    business_date, window_start, order, assignment = planning_entities()

    facts = materialize_planning_facts(business_date, [order], [assignment])

    assert facts.current_time == window_start


def test_open_ended_assignment_allows_service_after_delivery_window_arrival() -> None:
    business_date, _, order, assignment = planning_entities()
    facts = materialize_planning_facts(business_date, [order], [assignment])
    location_ids = tuple(location.location_id for location in facts.locations)
    size = len(location_ids)
    zero_matrix = tuple(tuple(0 for _ in range(size)) for _ in range(size))
    solver_input = build_solver_input(
        facts,
        RoutingMatrix(
            location_ids=location_ids,
            distance_matrix_meters=zero_matrix,
            duration_matrix_seconds=zero_matrix,
        ),
    )

    result = ORToolsSolver().solve(solver_input)

    assert result.unassigned_orders == ()
    assert len(result.routes) == 1
