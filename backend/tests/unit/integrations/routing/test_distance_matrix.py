from uuid import UUID

import pytest

from app.integrations.routing.contracts import RoutingLocation
from app.integrations.routing.distance_matrix import build_distance_time_matrix
from app.integrations.routing.provider import DeterministicRoutingProvider


LOCATION_A = UUID("10000000-0000-0000-0000-000000000001")
LOCATION_B = UUID("10000000-0000-0000-0000-000000000002")


def test_deterministic_provider_returns_meter_and_second_matrices() -> None:
    locations = (
        RoutingLocation(location_id=LOCATION_A, latitude=0.0, longitude=0.0),
        RoutingLocation(location_id=LOCATION_B, latitude=0.0, longitude=1.0),
    )

    matrix = build_distance_time_matrix(locations)

    assert matrix.location_ids == (LOCATION_A, LOCATION_B)
    assert matrix.distance_matrix_meters == (
        (0, 111_195),
        (111_195, 0),
    )
    assert matrix.duration_matrix_seconds == (
        (0, 11_120),
        (11_120, 0),
    )


def test_matrix_is_deterministic_and_preserves_input_order() -> None:
    locations = (
        RoutingLocation(location_id=LOCATION_B, latitude=1.0, longitude=1.0),
        RoutingLocation(location_id=LOCATION_A, latitude=1.0, longitude=1.0),
    )
    provider = DeterministicRoutingProvider(
        travel_speed_meters_per_second=5.0
    )

    first = build_distance_time_matrix(locations, provider=provider)
    second = build_distance_time_matrix(locations, provider=provider)

    assert first == second
    assert first.location_ids == (LOCATION_B, LOCATION_A)
    assert first.distance_matrix_meters == ((0, 0), (0, 0))
    assert first.duration_matrix_seconds == ((0, 0), (0, 0))


def test_empty_locations_return_empty_matrices() -> None:
    matrix = build_distance_time_matrix(())

    assert matrix.location_ids == ()
    assert matrix.distance_matrix_meters == ()
    assert matrix.duration_matrix_seconds == ()


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    (
        (91.0, 0.0),
        (-91.0, 0.0),
        (0.0, 181.0),
        (0.0, -181.0),
        (float("nan"), 0.0),
        (0.0, float("inf")),
    ),
)
def test_location_rejects_coordinates_outside_earth_bounds(
    latitude: float,
    longitude: float,
) -> None:
    with pytest.raises(ValueError):
        RoutingLocation(
            location_id=LOCATION_A,
            latitude=latitude,
            longitude=longitude,
        )


def test_matrix_rejects_duplicate_location_ids() -> None:
    locations = (
        RoutingLocation(location_id=LOCATION_A, latitude=0.0, longitude=0.0),
        RoutingLocation(location_id=LOCATION_A, latitude=1.0, longitude=1.0),
    )

    with pytest.raises(ValueError, match="unique"):
        build_distance_time_matrix(locations)


@pytest.mark.parametrize(
    "speed",
    (0.0, -1.0, float("nan"), float("inf")),
)
def test_provider_rejects_non_positive_or_non_finite_speed(speed: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        DeterministicRoutingProvider(travel_speed_meters_per_second=speed)
