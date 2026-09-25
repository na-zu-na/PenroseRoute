from datetime import date, datetime, timezone
from uuid import UUID

from app.integrations.optimization.contracts import (
    FrozenTask,
    SolverInput,
    SolverOrder,
    SolverResult,
    SolverRoute,
    SolverStatus,
    SolverStop,
    SolverStopType,
    SolverUnassignedOrder,
    SolverVehicle,
)
from app.integrations.optimization.result_validator import SolverResultValidator


ORDER_ID = UUID("40000000-0000-0000-0000-000000000001")
VEHICLE_ID = UUID("50000000-0000-0000-0000-000000000001")
PICKUP_ID = UUID("10000000-0000-0000-0000-000000000002")
DELIVERY_ID = UUID("10000000-0000-0000-0000-000000000004")
WRONG_LOCATION_ID = UUID("10000000-0000-0000-0000-000000000099")


def make_input(*, frozen_tasks: tuple[FrozenTask, ...] = ()) -> SolverInput:
    return SolverInput(
        business_date=date(2026, 9, 25),
        current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
        orders=(
            SolverOrder(
                order_id=ORDER_ID,
                pickup_location_id=PICKUP_ID,
                handover_location_id=None,
                delivery_location_id=DELIVERY_ID,
                ready_time_seconds=0,
                delivery_window_start_seconds=600,
                delivery_window_end_seconds=3_600,
                pickup_service_seconds=300,
                handover_service_seconds=0,
                delivery_service_seconds=300,
                demand_load_units=2,
            ),
        ),
        vehicles=(
            SolverVehicle(
                vehicle_id=VEHICLE_ID,
                capacity_load_units=4,
                start_location_id=PICKUP_ID,
                available_from_seconds=0,
                available_until_seconds=14_400,
            ),
        ),
        frozen_tasks=frozen_tasks,
        recovery_scope=None,
        location_ids=(PICKUP_ID, DELIVERY_ID),
        distance_matrix_meters=((0, 8_000), (8_000, 0)),
        duration_matrix_seconds=((0, 1_200), (1_200, 0)),
    )


def make_stop(
    stop_type: SolverStopType,
    sequence_no: int,
    location_id: UUID,
) -> SolverStop:
    load_change = 2 if stop_type is SolverStopType.PICKUP else -2
    return SolverStop(
        order_id=ORDER_ID,
        location_id=location_id,
        stop_type=stop_type,
        sequence_no=sequence_no,
        arrival_time_seconds=sequence_no * 600,
        departure_time_seconds=sequence_no * 600 + 300,
        service_duration_seconds=300,
        load_change_load_units=load_change,
    )


def make_route(*stops: SolverStop) -> SolverRoute:
    return SolverRoute(
        vehicle_id=VEHICLE_ID,
        stops=stops,
        distance_meters=8_000,
        duration_seconds=1_500,
    )


def test_validator_accepts_complete_feasible_result() -> None:
    result = SolverResult(
        status=SolverStatus.FEASIBLE,
        routes=(
            make_route(
                make_stop(SolverStopType.PICKUP, 1, PICKUP_ID),
                make_stop(SolverStopType.DELIVERY, 2, DELIVERY_ID),
            ),
        ),
        unassigned_orders=(),
        total_distance_meters=8_000,
        total_duration_seconds=1_500,
        diagnostic=None,
    )

    assert SolverResultValidator().validate(make_input(), result) == ()


def test_validator_rejects_duplicate_assignment_and_unassigned_order() -> None:
    result = SolverResult(
        status=SolverStatus.FEASIBLE,
        routes=(
            make_route(
                make_stop(SolverStopType.PICKUP, 1, PICKUP_ID),
                make_stop(SolverStopType.DELIVERY, 2, DELIVERY_ID),
            ),
        ),
        unassigned_orders=(
            SolverUnassignedOrder(
                order_id=ORDER_ID,
                reason_code="NO_FEASIBLE_ROUTE",
                reason_detail=None,
            ),
        ),
        total_distance_meters=8_000,
        total_duration_seconds=1_500,
        diagnostic=None,
    )

    issues = SolverResultValidator().validate(make_input(), result)

    assert "ORDER_DUPLICATED" in {issue.code for issue in issues}


def test_validator_rejects_delivery_before_pickup() -> None:
    result = SolverResult(
        status=SolverStatus.FEASIBLE,
        routes=(
            make_route(
                make_stop(SolverStopType.DELIVERY, 1, DELIVERY_ID),
                make_stop(SolverStopType.PICKUP, 2, PICKUP_ID),
            ),
        ),
        unassigned_orders=(),
        total_distance_meters=8_000,
        total_duration_seconds=1_500,
        diagnostic=None,
    )

    issues = SolverResultValidator().validate(make_input(), result)

    assert "STOP_PRECEDENCE_INVALID" in {issue.code for issue in issues}


def test_validator_rejects_origin_and_delivery_location_mismatches() -> None:
    result = SolverResult(
        status=SolverStatus.FEASIBLE,
        routes=(
            make_route(
                make_stop(SolverStopType.PICKUP, 1, WRONG_LOCATION_ID),
                make_stop(SolverStopType.DELIVERY, 2, WRONG_LOCATION_ID),
            ),
        ),
        unassigned_orders=(),
        total_distance_meters=8_000,
        total_duration_seconds=1_500,
        diagnostic=None,
    )

    issues = SolverResultValidator().validate(make_input(), result)
    codes = {issue.code for issue in issues}

    assert "ORIGIN_LOCATION_MISMATCH" in codes
    assert "DELIVERY_LOCATION_MISMATCH" in codes


def test_validator_requires_frozen_task_on_its_vehicle() -> None:
    pickup = make_stop(SolverStopType.PICKUP, 1, PICKUP_ID)
    solver_input = make_input(
        frozen_tasks=(FrozenTask(vehicle_id=VEHICLE_ID, stop=pickup),),
    )
    result = SolverResult(
        status=SolverStatus.FEASIBLE,
        routes=(),
        unassigned_orders=(
            SolverUnassignedOrder(
                order_id=ORDER_ID,
                reason_code="NO_FEASIBLE_ROUTE",
                reason_detail=None,
            ),
        ),
        total_distance_meters=0,
        total_duration_seconds=0,
        diagnostic=None,
    )

    issues = SolverResultValidator().validate(solver_input, result)

    assert "FROZEN_TASK_MISSING" in {issue.code for issue in issues}


def test_validator_rejects_routes_for_non_feasible_result() -> None:
    result = SolverResult(
        status=SolverStatus.INFEASIBLE,
        routes=(make_route(),),
        unassigned_orders=(),
        total_distance_meters=8_000,
        total_duration_seconds=1_500,
        diagnostic="No feasible assignment",
    )

    issues = SolverResultValidator().validate(make_input(), result)

    assert "STATUS_RESULT_CONFLICT" in {issue.code for issue in issues}
