from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from app.integrations.optimization.contracts import (
    FrozenTask,
    RecoveryScope,
    SolverInput,
    SolverOrder,
    SolverStop,
    SolverStopType,
    SolverVehicle,
)


ORDER_ID = UUID("40000000-0000-0000-0000-000000000001")
VEHICLE_ID = UUID("50000000-0000-0000-0000-000000000001")
PICKUP_ID = UUID("10000000-0000-0000-0000-000000000002")
DELIVERY_ID = UUID("10000000-0000-0000-0000-000000000004")


def test_solver_input_expresses_units_scope_and_frozen_tasks() -> None:
    order = SolverOrder(
        order_id=ORDER_ID,
        pickup_location_id=PICKUP_ID,
        handover_location_id=None,
        delivery_location_id=DELIVERY_ID,
        ready_time_seconds=300,
        delivery_window_start_seconds=1_800,
        delivery_window_end_seconds=5_400,
        pickup_service_seconds=300,
        handover_service_seconds=0,
        delivery_service_seconds=300,
        demand_load_units=2,
    )
    vehicle = SolverVehicle(
        vehicle_id=VEHICLE_ID,
        capacity_load_units=4,
        start_location_id=PICKUP_ID,
        available_from_seconds=0,
        available_until_seconds=14_400,
    )
    frozen_stop = SolverStop(
        order_id=ORDER_ID,
        location_id=PICKUP_ID,
        stop_type=SolverStopType.PICKUP,
        sequence_no=1,
        arrival_time_seconds=300,
        departure_time_seconds=600,
        service_duration_seconds=300,
        load_change_load_units=2,
    )

    solver_input = SolverInput(
        business_date=date(2026, 9, 25),
        current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
        orders=(order,),
        vehicles=(vehicle,),
        frozen_tasks=(FrozenTask(vehicle_id=VEHICLE_ID, stop=frozen_stop),),
        recovery_scope=RecoveryScope.AFFECTED_ROUTE,
        location_ids=(PICKUP_ID, DELIVERY_ID),
        distance_matrix_meters=((0, 8_000), (8_000, 0)),
        duration_matrix_seconds=((0, 1_200), (1_200, 0)),
    )

    assert solver_input.orders[0].demand_load_units == 2
    assert solver_input.distance_matrix_meters[0][1] == 8_000
    assert solver_input.duration_matrix_seconds[0][1] == 1_200
    assert solver_input.frozen_tasks[0].vehicle_id == VEHICLE_ID
    assert solver_input.recovery_scope is RecoveryScope.AFFECTED_ROUTE
    with pytest.raises(FrozenInstanceError):
        solver_input.orders = ()  # type: ignore[misc]


def test_handover_replaces_pickup_for_recovery_order() -> None:
    order = SolverOrder(
        order_id=ORDER_ID,
        pickup_location_id=None,
        handover_location_id=PICKUP_ID,
        delivery_location_id=DELIVERY_ID,
        ready_time_seconds=0,
        delivery_window_start_seconds=600,
        delivery_window_end_seconds=3_600,
        pickup_service_seconds=0,
        handover_service_seconds=300,
        delivery_service_seconds=300,
        demand_load_units=1,
    )

    assert order.handover_location_id == PICKUP_ID
    assert order.pickup_location_id is None


def test_order_rejects_ambiguous_pickup_and_handover_origin() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        SolverOrder(
            order_id=ORDER_ID,
            pickup_location_id=PICKUP_ID,
            handover_location_id=PICKUP_ID,
            delivery_location_id=DELIVERY_ID,
            ready_time_seconds=0,
            delivery_window_start_seconds=600,
            delivery_window_end_seconds=3_600,
            pickup_service_seconds=300,
            handover_service_seconds=300,
            delivery_service_seconds=300,
            demand_load_units=1,
        )


def test_solver_input_rejects_naive_time_and_invalid_matrix_shape() -> None:
    vehicle = SolverVehicle(
        vehicle_id=VEHICLE_ID,
        capacity_load_units=4,
        start_location_id=PICKUP_ID,
        available_from_seconds=0,
        available_until_seconds=14_400,
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        SolverInput(
            business_date=date(2026, 9, 25),
            current_time=datetime(2026, 9, 25, 9),
            orders=(),
            vehicles=(vehicle,),
            frozen_tasks=(),
            recovery_scope=None,
            location_ids=(PICKUP_ID,),
            distance_matrix_meters=((0,),),
            duration_matrix_seconds=((0,),),
        )

    with pytest.raises(ValueError, match="square"):
        SolverInput(
            business_date=date(2026, 9, 25),
            current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
            orders=(),
            vehicles=(vehicle,),
            frozen_tasks=(),
            recovery_scope=None,
            location_ids=(PICKUP_ID, DELIVERY_ID),
            distance_matrix_meters=((0,),),
            duration_matrix_seconds=((0, 1), (1, 0)),
        )


def test_solver_input_rejects_order_location_missing_from_matrix() -> None:
    order = SolverOrder(
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
        demand_load_units=1,
    )
    vehicle = SolverVehicle(
        vehicle_id=VEHICLE_ID,
        capacity_load_units=4,
        start_location_id=PICKUP_ID,
        available_from_seconds=0,
        available_until_seconds=14_400,
    )

    with pytest.raises(ValueError, match="referenced locations"):
        SolverInput(
            business_date=date(2026, 9, 25),
            current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
            orders=(order,),
            vehicles=(vehicle,),
            frozen_tasks=(),
            recovery_scope=None,
            location_ids=(PICKUP_ID,),
            distance_matrix_meters=((0,),),
            duration_matrix_seconds=((0,),),
        )


def test_solver_input_rejects_vehicle_start_missing_from_matrix() -> None:
    vehicle = SolverVehicle(
        vehicle_id=VEHICLE_ID,
        capacity_load_units=4,
        start_location_id=DELIVERY_ID,
        available_from_seconds=0,
        available_until_seconds=14_400,
    )

    with pytest.raises(ValueError, match="referenced locations"):
        SolverInput(
            business_date=date(2026, 9, 25),
            current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
            orders=(),
            vehicles=(vehicle,),
            frozen_tasks=(),
            recovery_scope=None,
            location_ids=(PICKUP_ID,),
            distance_matrix_meters=((0,),),
            duration_matrix_seconds=((0,),),
        )


def test_solver_input_rejects_frozen_task_location_missing_from_matrix() -> None:
    vehicle = SolverVehicle(
        vehicle_id=VEHICLE_ID,
        capacity_load_units=4,
        start_location_id=PICKUP_ID,
        available_from_seconds=0,
        available_until_seconds=14_400,
    )
    frozen_stop = SolverStop(
        order_id=ORDER_ID,
        location_id=DELIVERY_ID,
        stop_type=SolverStopType.DELIVERY,
        sequence_no=1,
        arrival_time_seconds=300,
        departure_time_seconds=600,
        service_duration_seconds=300,
        load_change_load_units=-1,
    )

    with pytest.raises(ValueError, match="referenced locations"):
        SolverInput(
            business_date=date(2026, 9, 25),
            current_time=datetime(2026, 9, 25, 9, tzinfo=timezone.utc),
            orders=(),
            vehicles=(vehicle,),
            frozen_tasks=(
                FrozenTask(vehicle_id=VEHICLE_ID, stop=frozen_stop),
            ),
            recovery_scope=RecoveryScope.AFFECTED_ROUTE,
            location_ids=(PICKUP_ID,),
            distance_matrix_meters=((0,),),
            duration_matrix_seconds=((0,),),
        )
