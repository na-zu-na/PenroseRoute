"""Read-only, repeatable GPS-style positions derived from a delivery plan."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import cos, hypot, isfinite, radians
from typing import Iterable
from uuid import UUID

from app.core.errors import IntegrationError


@dataclass(frozen=True)
class RoutePosition:
    latitude: float
    longitude: float
    motion: str
    next_stop_id: UUID | None


@dataclass(frozen=True)
class SimulatedVehicle:
    vehicle_id: UUID
    vehicle_code: str
    route_id: UUID
    route_no: int
    latitude: float
    longitude: float
    motion: str
    next_stop_id: UUID | None
    path: tuple[tuple[float, float], ...]
    source: str = "SIMULATED"


@dataclass(frozen=True)
class SimulationSnapshot:
    generated_at: datetime
    simulated_at: datetime
    cycle_seconds: int
    vehicles: tuple[SimulatedVehicle, ...]


def _coordinates(location) -> tuple[float, float]:
    return float(location.latitude), float(location.longitude)


def _between(start, end, fraction: float) -> tuple[float, float]:
    a_lat, a_lon = _coordinates(start)
    b_lat, b_lon = _coordinates(end)
    fraction = max(0.0, min(1.0, fraction))
    return a_lat + (b_lat - a_lat) * fraction, a_lon + (b_lon - a_lon) * fraction


def _fraction(at: datetime, start: datetime, end: datetime) -> float:
    seconds = (end - start).total_seconds()
    return (at - start).total_seconds() / seconds if seconds > 0 else 1.0


def _road_data(route, stops) -> tuple[list[list[float]], list[int]] | None:
    metrics = getattr(route, "route_metrics", None) or {}
    if not isinstance(metrics, dict):
        return None
    if metrics.get("geometry_provider") != "OSRM":
        return None
    geometry = getattr(route, "route_geometry", None) or {}
    points = geometry.get("coordinates") if isinstance(geometry, dict) and geometry.get("type") == "LineString" else None
    ends = metrics.get("road_leg_end_indices")
    valid_points = isinstance(points, list) and len(points) >= 2 and all(
        isinstance(point, list) and len(point) == 2 and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
            for value in point
        ) and -180 <= point[0] <= 180 and -90 <= point[1] <= 90
        for point in points
    )
    valid_ends = valid_points and isinstance(ends, list) and bool(ends) and (
        len(ends) in (len(stops), len(stops) + 1)
        and all(isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(points) for index in ends)
    )
    if not valid_ends or any(left > right for left, right in zip(ends, ends[1:])) or ends[-1] != len(points) - 1:
        raise IntegrationError(code="ROAD_GEOMETRY_INVALID", message="Road route leg metadata is invalid")
    return points, ends


def _along_road(points: list[list[float]], start: int, end: int, fraction: float) -> tuple[float, float]:
    segment = points[start:end + 1]
    if len(segment) == 1:
        return segment[0][1], segment[0][0]
    lengths = [
        hypot((b[0] - a[0]) * cos(radians((a[1] + b[1]) / 2)), b[1] - a[1])
        for a, b in zip(segment, segment[1:])
    ]
    remaining = sum(lengths) * max(0.0, min(1.0, fraction))
    for (a, b), length in zip(zip(segment, segment[1:]), lengths):
        if remaining <= length and length > 0:
            portion = remaining / length
            return a[1] + (b[1] - a[1]) * portion, a[0] + (b[0] - a[0]) * portion
        remaining -= length
    return segment[-1][1], segment[-1][0]


def position_for_route(route, simulated_at: datetime) -> RoutePosition:
    """Interpolate between planned stops and stay still during service windows."""
    stops = sorted(route.stops, key=lambda stop: stop.sequence_no)
    road = _road_data(route, stops)
    if simulated_at >= route.planned_end_at:
        lat, lon = (road[0][-1][1], road[0][-1][0]) if road else _coordinates(route.end_location)
        return RoutePosition(lat, lon, "FINISHED", None)
    if simulated_at <= route.planned_start_at:
        lat, lon = (road[0][0][1], road[0][0][0]) if road else _coordinates(route.start_location)
        return RoutePosition(lat, lon, "BEFORE_START", stops[0].id if stops else None)

    origin = route.start_location
    departure = route.planned_start_at
    for index, stop in enumerate(stops):
        if simulated_at < stop.planned_arrival_at:
            progress = _fraction(simulated_at, departure, stop.planned_arrival_at)
            lat, lon = (
                _along_road(road[0], 0 if index == 0 else road[1][index - 1], road[1][index], progress)
                if road else _between(origin, stop.location, progress)
            )
            return RoutePosition(lat, lon, "MOVING", stop.id)
        if simulated_at <= stop.planned_departure_at:
            lat, lon = (road[0][road[1][index]][1], road[0][road[1][index]][0]) if road else _coordinates(stop.location)
            return RoutePosition(lat, lon, "AT_STOP", stop.id)
        origin = stop.location
        departure = stop.planned_departure_at

    progress = _fraction(simulated_at, departure, route.planned_end_at)
    lat, lon = (
        _along_road(road[0], road[1][len(stops) - 1] if stops else 0, road[1][-1], progress)
        if road else _between(origin, route.end_location, progress)
    )
    return RoutePosition(lat, lon, "MOVING", None)


def simulated_positions(
    routes: Iterable,
    *,
    generated_at: datetime,
    cycle_seconds: int,
    elapsed_seconds: float,
) -> SimulationSnapshot:
    routes = sorted(routes, key=lambda route: route.route_no)
    if not routes:
        return SimulationSnapshot(generated_at, generated_at, cycle_seconds, ())

    plan_start = min(route.planned_start_at for route in routes)
    plan_end = max(route.planned_end_at for route in routes)
    # Reserve the last 10% of each cycle at the destination before restarting.
    fraction = max(0.0, min(1.0, elapsed_seconds / (cycle_seconds * 0.9)))
    simulated_at = plan_start + timedelta(
        seconds=(plan_end - plan_start).total_seconds() * fraction
    )
    vehicles = []
    for route in routes:
        stops = sorted(route.stops, key=lambda stop: stop.sequence_no)
        position = position_for_route(route, simulated_at)
        road = _road_data(route, stops)
        path_locations = [route.start_location, *(stop.location for stop in stops), route.end_location]
        vehicles.append(SimulatedVehicle(
            vehicle_id=route.vehicle_id,
            vehicle_code=route.vehicle.vehicle_code,
            route_id=route.id,
            route_no=route.route_no,
            latitude=position.latitude,
            longitude=position.longitude,
            motion=position.motion,
            next_stop_id=position.next_stop_id,
            path=(tuple((float(lon), float(lat)) for lon, lat in road[0]) if road else
                  tuple((float(loc.longitude), float(loc.latitude)) for loc in path_locations)),
        ))
    return SimulationSnapshot(generated_at, simulated_at, cycle_seconds, tuple(vehicles))
