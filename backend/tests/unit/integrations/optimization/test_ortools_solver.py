from datetime import date, datetime, timezone
from uuid import UUID

from app.integrations.optimization.contracts import (
    FrozenTask,
    RecoveryScope,
    SolverInput,
    SolverOrder,
    SolverStatus,
    SolverStop,
    SolverStopType,
    SolverVehicle,
)
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.optimization.result_validator import SolverResultValidator


HUB = UUID("10000000-0000-0000-0000-000000000001")
PICKUP_A = UUID("10000000-0000-0000-0000-000000000002")
DELIVERY_A = UUID("10000000-0000-0000-0000-000000000003")
PICKUP_B = UUID("10000000-0000-0000-0000-000000000004")
DELIVERY_B = UUID("10000000-0000-0000-0000-000000000005")
VEHICLE_A = UUID("50000000-0000-0000-0000-000000000001")
VEHICLE_B = UUID("50000000-0000-0000-0000-000000000002")
ORDER_A = UUID("40000000-0000-0000-0000-000000000001")
ORDER_B = UUID("40000000-0000-0000-0000-000000000002")


def order(
    order_id: UUID,
    pickup_id: UUID,
    delivery_id: UUID,
    *,
    demand: int = 1,
    ready: int = 0,
    window_start: int = 0,
    window_end: int = 1_000,
    handover: bool = False,
) -> SolverOrder:
    return SolverOrder(
        order_id=order_id,
        pickup_location_id=None if handover else pickup_id,
        handover_location_id=pickup_id if handover else None,
        delivery_location_id=delivery_id,
        ready_time_seconds=ready,
        delivery_window_start_seconds=window_start,
        delivery_window_end_seconds=window_end,
        pickup_service_seconds=0 if handover else 10,
        handover_service_seconds=10 if handover else 0,
        delivery_service_seconds=10,
        demand_load_units=demand,
    )


def vehicle(
    vehicle_id: UUID,
    start_location_id: UUID,
    *,
    capacity: int = 2,
    available_until: int = 2_000,
) -> SolverVehicle:
    return SolverVehicle(
        vehicle_id=vehicle_id,
        capacity_load_units=capacity,
        start_location_id=start_location_id,
        available_from_seconds=0,
        available_until_seconds=available_until,
    )


def solver_input(
    *,
    orders: tuple[SolverOrder, ...],
    vehicles: tuple[SolverVehicle, ...],
    location_ids: tuple[UUID, ...],
    duration_matrix: tuple[tuple[int, ...], ...],
    frozen_tasks: tuple[FrozenTask, ...] = (),
    scope: RecoveryScope | None = None,
) -> SolverInput:
    distance_matrix = tuple(
        tuple(value * 10 for value in row) for row in duration_matrix
    )
    return SolverInput(
        business_date=date(2026, 9, 25),
        current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
        orders=orders,
        vehicles=vehicles,
        frozen_tasks=frozen_tasks,
        recovery_scope=scope,
        location_ids=location_ids,
        distance_matrix_meters=distance_matrix,
        duration_matrix_seconds=duration_matrix,
    )


def test_solver_builds_feasible_pickup_delivery_route() -> None:
    data = solver_input(
        orders=(
            order(
                ORDER_A,
                PICKUP_A,
                DELIVERY_A,
                ready=100,
                window_start=150,
                window_end=500,
            ),
        ),
        vehicles=(vehicle(VEHICLE_A, HUB),),
        location_ids=(HUB, PICKUP_A, DELIVERY_A),
        duration_matrix=((0, 20, 40), (20, 0, 30), (40, 30, 0)),
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert result.unassigned_orders == ()
    assert len(result.routes) == 1
    stops = result.routes[0].stops
    assert [stop.stop_type for stop in stops] == [
        SolverStopType.PICKUP,
        SolverStopType.DELIVERY,
    ]
    assert stops[0].arrival_time_seconds >= 100
    assert 150 <= stops[1].arrival_time_seconds <= 500
    assert stops[0].departure_time_seconds - stops[0].arrival_time_seconds == 10
    assert stops[1].departure_time_seconds - stops[1].arrival_time_seconds == 10
    assert SolverResultValidator().validate(data, result) == ()


def test_solver_marks_order_unassigned_when_capacity_is_insufficient() -> None:
    data = solver_input(
        orders=(order(ORDER_A, PICKUP_A, DELIVERY_A, demand=3),),
        vehicles=(vehicle(VEHICLE_A, HUB, capacity=2),),
        location_ids=(HUB, PICKUP_A, DELIVERY_A),
        duration_matrix=((0, 10, 20), (10, 0, 10), (20, 10, 0)),
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert result.routes == ()
    assert len(result.unassigned_orders) == 1
    assert result.unassigned_orders[0].order_id == ORDER_A
    assert result.unassigned_orders[0].reason_code == "CAPACITY_INFEASIBLE"


def test_solver_marks_order_unassigned_when_delivery_window_is_infeasible() -> None:
    data = solver_input(
        orders=(
            order(
                ORDER_A,
                PICKUP_A,
                DELIVERY_A,
                window_end=50,
            ),
        ),
        vehicles=(vehicle(VEHICLE_A, HUB),),
        location_ids=(HUB, PICKUP_A, DELIVERY_A),
        duration_matrix=((0, 100, 200), (100, 0, 100), (200, 100, 0)),
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert result.routes == ()
    assert result.unassigned_orders[0].reason_code == "TIME_WINDOW_INFEASIBLE"


def test_solver_uses_multiple_vehicles_for_tight_independent_routes() -> None:
    data = solver_input(
        orders=(
            order(ORDER_A, PICKUP_A, DELIVERY_A, window_end=40),
            order(ORDER_B, PICKUP_B, DELIVERY_B, window_end=40),
        ),
        vehicles=(
            vehicle(VEHICLE_A, PICKUP_A),
            vehicle(VEHICLE_B, PICKUP_B),
        ),
        location_ids=(PICKUP_A, DELIVERY_A, PICKUP_B, DELIVERY_B),
        duration_matrix=(
            (0, 10, 500, 500),
            (10, 0, 500, 500),
            (500, 500, 0, 10),
            (500, 500, 10, 0),
        ),
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert result.unassigned_orders == ()
    assert len(result.routes) == 2
    assert {route.vehicle_id for route in result.routes} == {
        VEHICLE_A,
        VEHICLE_B,
    }


def test_solver_returns_assigned_and_unassigned_orders_together() -> None:
    data = solver_input(
        orders=(
            order(ORDER_A, PICKUP_A, DELIVERY_A, demand=1),
            order(ORDER_B, PICKUP_B, DELIVERY_B, demand=3),
        ),
        vehicles=(vehicle(VEHICLE_A, HUB, capacity=2),),
        location_ids=(HUB, PICKUP_A, DELIVERY_A, PICKUP_B, DELIVERY_B),
        duration_matrix=(
            (0, 10, 20, 10, 20),
            (10, 0, 10, 10, 20),
            (20, 10, 0, 20, 10),
            (10, 10, 20, 0, 10),
            (20, 20, 10, 10, 0),
        ),
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert {stop.order_id for route in result.routes for stop in route.stops} == {
        ORDER_A
    }
    assert [item.order_id for item in result.unassigned_orders] == [ORDER_B]
    assert SolverResultValidator().validate(data, result) == ()


def test_solver_supports_handover_precedence_and_recovery_scope() -> None:
    data = solver_input(
        orders=(
            order(
                ORDER_A,
                PICKUP_A,
                DELIVERY_A,
                handover=True,
                window_end=500,
            ),
        ),
        vehicles=(vehicle(VEHICLE_A, HUB),),
        location_ids=(HUB, PICKUP_A, DELIVERY_A),
        duration_matrix=((0, 20, 40), (20, 0, 20), (40, 20, 0)),
        scope=RecoveryScope.AFFECTED_ROUTE,
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert [stop.stop_type for stop in result.routes[0].stops] == [
        SolverStopType.HANDOVER,
        SolverStopType.DELIVERY,
    ]
    assert SolverResultValidator().validate(data, result) == ()


def test_solver_preserves_frozen_pickup_and_solves_remaining_delivery() -> None:
    frozen_pickup = SolverStop(
        order_id=ORDER_A,
        location_id=PICKUP_A,
        stop_type=SolverStopType.PICKUP,
        sequence_no=1,
        arrival_time_seconds=10,
        departure_time_seconds=20,
        service_duration_seconds=10,
        load_change_load_units=1,
    )
    data = solver_input(
        orders=(order(ORDER_A, PICKUP_A, DELIVERY_A, window_end=500),),
        vehicles=(vehicle(VEHICLE_A, HUB),),
        location_ids=(HUB, PICKUP_A, DELIVERY_A),
        duration_matrix=((0, 10, 30), (10, 0, 20), (30, 20, 0)),
        frozen_tasks=(
            FrozenTask(vehicle_id=VEHICLE_A, stop=frozen_pickup),
        ),
        scope=RecoveryScope.AFFECTED_ROUTE,
    )

    result = ORToolsSolver().solve(data)

    assert result.status is SolverStatus.FEASIBLE
    assert result.unassigned_orders == ()
    assert result.routes[0].vehicle_id == VEHICLE_A
    assert result.routes[0].stops[0] == frozen_pickup
    assert result.routes[0].stops[1].stop_type is SolverStopType.DELIVERY
    assert result.routes[0].stops[1].sequence_no == 2
    assert SolverResultValidator().validate(data, result) == ()
