"""Road distance matrix and ordered route geometry from an OSRM server."""

import json
from dataclasses import dataclass
from math import ceil, cos, hypot, isfinite, radians
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.core.errors import IntegrationError
from app.integrations.routing.contracts import RoutingLocation, RoutingMatrix


@dataclass(frozen=True, slots=True)
class RoadRoute:
    geometry: dict
    leg_end_indices: tuple[int, ...]
    distance_meters: int
    duration_seconds: int


class OsrmRoutingProvider:
    def __init__(self, base_url: str, *, timeout_seconds: float = 8) -> None:
        if not base_url.startswith(("http://", "https://")) or timeout_seconds <= 0:
            raise ValueError("OSRM URL and timeout must be valid")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str) -> dict:
        try:
            with urlopen(self.base_url + path, timeout=self.timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as error:
            try:
                code = json.load(error).get("code")
            except (ValueError, AttributeError):
                code = None
            if code in ("NoRoute", "NoSegment"):
                raise IntegrationError(code="ROAD_ROUTING_NO_ROUTE", message=f"OSRM returned {code}") from error
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM rejected the routing request") from error
        except (URLError, TimeoutError, OSError) as error:
            raise IntegrationError(code="ROAD_ROUTING_UNAVAILABLE", message="OSRM request failed") from error
        except (ValueError, UnicodeError) as error:
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM returned invalid JSON") from error
        if isinstance(payload, dict) and payload.get("code") in ("NoRoute", "NoSegment"):
            raise IntegrationError(code="ROAD_ROUTING_NO_ROUTE", message=f"OSRM returned {payload['code']}")
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM could not route the requested locations")
        return payload

    @staticmethod
    def _coordinates(locations: tuple[RoutingLocation, ...]) -> str:
        return ";".join(f"{location.longitude},{location.latitude}" for location in locations)

    @staticmethod
    def _number(value, *, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message=f"OSRM {label} is invalid")
        return float(value)

    @classmethod
    def _snaps(cls, waypoints, count: int) -> None:
        if not isinstance(waypoints, list) or len(waypoints) != count:
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM waypoint count is invalid")
        for waypoint in waypoints:
            if not isinstance(waypoint, dict) or cls._number(waypoint.get("distance"), label="snap distance") > 250:
                raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM snapped a location too far from the road")

    def build_matrix(self, locations: tuple[RoutingLocation, ...]) -> RoutingMatrix:
        location_ids = tuple(location.location_id for location in locations)
        if not locations:
            return RoutingMatrix((), (), ())
        if len(locations) == 1:
            return RoutingMatrix(location_ids, ((0,),), ((0,),))
        payload = self._request(
            f"/table/v1/driving/{self._coordinates(locations)}?annotations=duration,distance"
        )
        count = len(locations)
        self._snaps(payload.get("sources"), count)
        self._snaps(payload.get("destinations"), count)

        def matrix(name: str, convert) -> tuple[tuple[int, ...], ...]:
            rows = payload.get(name)
            if not isinstance(rows, list) or len(rows) != count or any(
                not isinstance(row, list) or len(row) != count for row in rows
            ):
                raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message=f"OSRM {name} matrix shape is invalid")
            return tuple(tuple(convert(self._number(value, label=name)) for value in row) for row in rows)

        durations = matrix("durations", ceil)
        distances = matrix("distances", round)
        return RoutingMatrix(location_ids, distances, durations)

    @staticmethod
    def _line(raw) -> list[list[float]]:
        if not isinstance(raw, dict) or raw.get("type") != "LineString":
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM step geometry is missing")
        points = raw.get("coordinates")
        if not isinstance(points, list) or len(points) < 2:
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM step geometry is too short")
        checked = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(
                isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value)
                for value in point
            ) or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
                raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM step coordinate is invalid")
            checked.append([float(point[0]), float(point[1])])
        return checked

    def build_route(self, waypoints: tuple[RoutingLocation, ...]) -> RoadRoute:
        if len(waypoints) < 2:
            raise ValueError("a route needs at least two waypoints")
        distinct = [waypoints[0]]
        positions = [0]
        for waypoint in waypoints[1:]:
            if (waypoint.longitude, waypoint.latitude) != (distinct[-1].longitude, distinct[-1].latitude):
                distinct.append(waypoint)
            positions.append(len(distinct) - 1)
        if len(distinct) == 1:
            payload = self._request(
                f"/nearest/v1/driving/{self._coordinates(tuple(distinct))}?number=1"
            )
            self._snaps(payload.get("waypoints"), 1)
            snapped = payload["waypoints"][0].get("location")
            point = self._line({"type": "LineString", "coordinates": [snapped, snapped]})[0]
            return RoadRoute({"type": "LineString", "coordinates": [point, point.copy()]},
                             tuple(1 for _ in waypoints[1:]), 0, 0)

        payload = self._request(
            f"/route/v1/driving/{self._coordinates(tuple(distinct))}"
            "?steps=true&geometries=geojson&overview=full"
        )
        self._snaps(payload.get("waypoints"), len(distinct))
        routes = payload.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM route is missing")
        route = routes[0]
        legs = route.get("legs")
        if not isinstance(legs, list) or len(legs) != len(distinct) - 1:
            raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM leg count is invalid")
        coordinates: list[list[float]] = []
        ends = [0]
        for leg in legs:
            steps = leg.get("steps") if isinstance(leg, dict) else None
            if not isinstance(steps, list) or not steps:
                raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM leg geometry is missing")
            for step_no, step in enumerate(steps):
                points = self._line(step.get("geometry") if isinstance(step, dict) else None)
                shared_point = bool(coordinates and coordinates[-1] == points[0])
                if coordinates and not shared_point:
                    previous = coordinates[-1]
                    latitude = radians((previous[1] + points[0][1]) / 2)
                    gap_meters = hypot(
                        (previous[0] - points[0][0]) * cos(latitude),
                        previous[1] - points[0][1],
                    ) * 111_195
                    if step_no != 0 or gap_meters > 2:
                        raise IntegrationError(code="ROAD_ROUTING_INVALID_RESPONSE", message="OSRM step geometry is disconnected")
                coordinates.extend(points[1:] if shared_point else points)
            ends.append(len(coordinates) - 1)
        return RoadRoute(
            geometry={"type": "LineString", "coordinates": coordinates},
            leg_end_indices=tuple(ends[position] for position in positions[1:]),
            distance_meters=round(self._number(route.get("distance"), label="route distance")),
            duration_seconds=ceil(self._number(route.get("duration"), label="route duration")),
        )
