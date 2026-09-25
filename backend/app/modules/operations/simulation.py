"""Read-only, repeatable GPS-style positions derived from a delivery plan."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from uuid import UUID


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


def position_for_route(route, simulated_at: datetime) -> RoutePosition:
    """Interpolate between planned stops and stay still during service windows."""
    stops = sorted(route.stops, key=lambda stop: stop.sequence_no)
    if simulated_at >= route.planned_end_at:
        lat, lon = _coordinates(route.end_location)
        return RoutePosition(lat, lon, "FINISHED", None)
    if simulated_at <= route.planned_start_at:
        lat, lon = _coordinates(route.start_location)
        return RoutePosition(lat, lon, "BEFORE_START", stops[0].id if stops else None)

    origin = route.start_location
    departure = route.planned_start_at
    for stop in stops:
        if simulated_at < stop.planned_arrival_at:
            lat, lon = _between(
                origin, stop.location,
                _fraction(simulated_at, departure, stop.planned_arrival_at),
            )
            return RoutePosition(lat, lon, "MOVING", stop.id)
        if simulated_at <= stop.planned_departure_at:
            lat, lon = _coordinates(stop.location)
            return RoutePosition(lat, lon, "AT_STOP", stop.id)
        origin = stop.location
        departure = stop.planned_departure_at

    lat, lon = _between(
        origin, route.end_location,
        _fraction(simulated_at, departure, route.planned_end_at),
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
            path=tuple((float(loc.longitude), float(loc.latitude)) for loc in path_locations),
        ))
    return SimulationSnapshot(generated_at, simulated_at, cycle_seconds, tuple(vehicles))
