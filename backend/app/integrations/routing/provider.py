"""The single P0 routing provider and its stable interface."""

from math import asin, ceil, cos, isfinite, radians, sin, sqrt
from typing import Protocol

from app.integrations.routing.contracts import RoutingLocation, RoutingMatrix


EARTH_RADIUS_METERS = 6_371_000


class RoutingProvider(Protocol):
    def build_matrix(
        self,
        locations: tuple[RoutingLocation, ...],
    ) -> RoutingMatrix: ...


class DeterministicRoutingProvider:
    """Development fallback using straight-line distance and fixed speed."""

    def __init__(self, *, travel_speed_meters_per_second: float = 10.0) -> None:
        if (
            not isfinite(travel_speed_meters_per_second)
            or travel_speed_meters_per_second <= 0
        ):
            raise ValueError("travel speed must be positive")
        self.travel_speed_meters_per_second = travel_speed_meters_per_second

    def build_matrix(
        self,
        locations: tuple[RoutingLocation, ...],
    ) -> RoutingMatrix:
        distances = tuple(
            tuple(self._distance_meters(origin, destination) for destination in locations)
            for origin in locations
        )
        durations = tuple(
            tuple(
                ceil(distance / self.travel_speed_meters_per_second)
                for distance in row
            )
            for row in distances
        )
        return RoutingMatrix(
            location_ids=tuple(location.location_id for location in locations),
            distance_matrix_meters=distances,
            duration_matrix_seconds=durations,
        )

    @staticmethod
    def _distance_meters(
        origin: RoutingLocation,
        destination: RoutingLocation,
    ) -> int:
        latitude_delta = radians(destination.latitude - origin.latitude)
        longitude_delta = radians(destination.longitude - origin.longitude)
        origin_latitude = radians(origin.latitude)
        destination_latitude = radians(destination.latitude)
        haversine = (
            sin(latitude_delta / 2) ** 2
            + cos(origin_latitude)
            * cos(destination_latitude)
            * sin(longitude_delta / 2) ** 2
        )
        central_angle = 2 * asin(sqrt(haversine))
        return round(EARTH_RADIUS_METERS * central_angle)
