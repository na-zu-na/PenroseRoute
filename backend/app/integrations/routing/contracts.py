"""Pure Python contracts for distance and travel-time matrices."""

from dataclasses import dataclass
from math import isfinite
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RoutingLocation:
    location_id: UUID
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be finite and between -90 and 90")
        if not isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be finite and between -180 and 180")


@dataclass(frozen=True, slots=True)
class RoutingMatrix:
    location_ids: tuple[UUID, ...]
    distance_matrix_meters: tuple[tuple[int, ...], ...]
    duration_matrix_seconds: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        size = len(self.location_ids)
        if len(set(self.location_ids)) != size:
            raise ValueError("location IDs must be unique")
        for name, matrix in (
            ("distance_matrix_meters", self.distance_matrix_meters),
            ("duration_matrix_seconds", self.duration_matrix_seconds),
        ):
            if len(matrix) != size or any(len(row) != size for row in matrix):
                raise ValueError(f"{name} must be square for all locations")
            if any(value < 0 for row in matrix for value in row):
                raise ValueError(f"{name} values must be non-negative")
