"""Application-facing entry point for routing matrix construction."""

from collections.abc import Iterable

from app.integrations.routing.contracts import RoutingLocation, RoutingMatrix
from app.integrations.routing.provider import (
    DeterministicRoutingProvider,
    RoutingProvider,
)


def build_distance_time_matrix(
    locations: Iterable[RoutingLocation],
    *,
    provider: RoutingProvider | None = None,
) -> RoutingMatrix:
    materialized_locations = tuple(locations)
    selected_provider = provider or DeterministicRoutingProvider()
    return selected_provider.build_matrix(materialized_locations)
