from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.errors import IntegrationError
from app.modules.operations.simulation import position_for_route, simulated_positions


def _location(latitude: float, longitude: float) -> SimpleNamespace:
    return SimpleNamespace(latitude=latitude, longitude=longitude)


def _route() -> SimpleNamespace:
    start = datetime(2026, 9, 26, 9, tzinfo=timezone.utc)
    pickup = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        sequence_no=1,
        location=_location(1.31, 103.81),
        planned_arrival_at=start + timedelta(minutes=3),
        planned_departure_at=start + timedelta(minutes=4),
    )
    delivery = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        sequence_no=2,
        location=_location(1.32, 103.82),
        planned_arrival_at=start + timedelta(minutes=8),
        planned_departure_at=start + timedelta(minutes=9),
    )
    return SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        route_no=1,
        vehicle_id=UUID("00000000-0000-0000-0000-000000000004"),
        vehicle=SimpleNamespace(vehicle_code="V01"),
        start_location=_location(1.30, 103.80),
        end_location=_location(1.33, 103.83),
        planned_start_at=start,
        planned_end_at=start + timedelta(minutes=10),
        stops=[delivery, pickup],
    )


def test_position_moves_between_stops_and_waits_during_service() -> None:
    route = _route()
    start = route.planned_start_at

    moving = position_for_route(route, start + timedelta(minutes=1, seconds=30))
    assert (moving.latitude, moving.longitude) == pytest.approx((1.305, 103.805))
    assert moving.next_stop_id == route.stops[1].id
    assert moving.motion == "MOVING"

    waiting = position_for_route(route, start + timedelta(minutes=3, seconds=30))
    assert (waiting.latitude, waiting.longitude) == pytest.approx((1.31, 103.81))
    assert waiting.motion == "AT_STOP"

    finished = position_for_route(route, start + timedelta(minutes=11))
    assert (finished.latitude, finished.longitude) == pytest.approx((1.33, 103.83))
    assert finished.motion == "FINISHED"


def test_snapshot_includes_all_routes_at_the_same_simulated_time() -> None:
    route = _route()
    second = _route()
    second.id = UUID("00000000-0000-0000-0000-000000000005")
    second.vehicle_id = UUID("00000000-0000-0000-0000-000000000006")
    second.route_no = 2
    second.vehicle.vehicle_code = "V02"

    snapshot = simulated_positions(
        [route, second],
        generated_at=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
        cycle_seconds=100,
        elapsed_seconds=45,
    )
    assert snapshot.simulated_at == route.planned_start_at + timedelta(minutes=5)
    assert [item.vehicle_code for item in snapshot.vehicles] == ["V01", "V02"]
    assert all(item.source == "SIMULATED" for item in snapshot.vehicles)


def test_snapshot_dwells_at_destination_before_restarting() -> None:
    route = _route()
    snapshot = simulated_positions(
        [route],
        generated_at=datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
        cycle_seconds=100,
        elapsed_seconds=95,
    )

    assert snapshot.simulated_at == route.planned_end_at
    vehicle = snapshot.vehicles[0]
    assert vehicle.motion == "FINISHED"
    assert (vehicle.latitude, vehicle.longitude) == pytest.approx((1.33, 103.83))


def _road_route() -> SimpleNamespace:
    route = _route()
    route.route_geometry = {"type": "LineString", "coordinates": [
        [103.8, 1.30], [103.8, 1.31], [103.81, 1.31],
        [103.82, 1.31], [103.82, 1.32],
        [103.83, 1.32], [103.83, 1.33],
    ]}
    route.route_metrics = {"geometry_provider": "OSRM", "road_leg_end_indices": [2, 4, 6]}
    return route


def test_road_position_follows_each_leg_and_dwell_uses_snapped_point() -> None:
    route = _road_route()
    start = route.planned_start_at
    first_move = position_for_route(route, start + timedelta(minutes=1, seconds=30))
    assert (first_move.latitude, first_move.longitude) == pytest.approx((1.31, 103.8), abs=0.00001)
    assert first_move.next_stop_id == route.stops[1].id

    dwell = position_for_route(route, start + timedelta(minutes=3, seconds=30))
    assert (dwell.latitude, dwell.longitude) == pytest.approx((1.31, 103.81))
    assert dwell.motion == "AT_STOP"

    second_move = position_for_route(route, start + timedelta(minutes=6))
    assert (second_move.latitude, second_move.longitude) == pytest.approx((1.31, 103.82), abs=0.00001)

    terminal = position_for_route(route, start + timedelta(minutes=9, seconds=30))
    assert (terminal.latitude, terminal.longitude) == pytest.approx((1.32, 103.83), abs=0.00001)
    assert position_for_route(route, start + timedelta(minutes=11)).longitude == 103.83


def test_simulated_snapshot_uses_the_same_road_line_as_vehicle_position() -> None:
    route = _road_route()
    snapshot = simulated_positions([route], generated_at=route.planned_start_at,
                                   cycle_seconds=100, elapsed_seconds=13.5)
    vehicle = snapshot.vehicles[0]
    assert vehicle.path == tuple(tuple(point) for point in route.route_geometry["coordinates"])
    assert (vehicle.longitude, vehicle.latitude) == pytest.approx((103.8, 1.31), abs=0.00001)


def test_zero_distance_road_leg_does_not_jump_or_divide_by_zero() -> None:
    route = _road_route()
    route.route_geometry["coordinates"] = [
        [103.8, 1.3], [103.8, 1.3], [103.82, 1.31], [103.82, 1.32],
        [103.83, 1.32], [103.83, 1.33],
    ]
    route.route_metrics["road_leg_end_indices"] = [1, 3, 5]
    moving = position_for_route(route, route.planned_start_at + timedelta(minutes=1))
    assert (moving.latitude, moving.longitude) == (1.3, 103.8)
    waiting = position_for_route(route, route.planned_start_at + timedelta(minutes=3, seconds=30))
    assert (waiting.latitude, waiting.longitude) == (1.3, 103.8)


def test_invalid_road_leg_metadata_is_not_silently_treated_as_a_road_route() -> None:
    route = _road_route()
    route.route_metrics["road_leg_end_indices"] = [2]
    with pytest.raises(IntegrationError, match="leg"):
        position_for_route(route, route.planned_start_at + timedelta(minutes=1))


def test_empty_road_leg_metadata_reports_a_routing_error() -> None:
    route = _road_route()
    route.stops = []
    route.route_metrics["road_leg_end_indices"] = []
    with pytest.raises(IntegrationError, match="leg"):
        position_for_route(route, route.planned_start_at + timedelta(minutes=1))
