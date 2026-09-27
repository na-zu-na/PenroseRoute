"""Application-facing entry point for routing matrix construction."""

from collections.abc import Iterable

from app.integrations.routing.contracts import RoutingLocation, RoutingMatrix
from app.integrations.routing.provider import (
    DeterministicRoutingProvider,
    RoutingProvider,
)
from app.integrations.routing.osrm import OsrmRoutingProvider
from app.core.config import get_settings
from app.core.errors import IntegrationError


def get_routing_provider() -> RoutingProvider:
    settings = get_settings()
    if settings.routing_provider == "osrm":
        if not settings.osrm_base_url:
            raise IntegrationError(code="ROAD_ROUTING_NOT_CONFIGURED", message="OSRM_BASE_URL is required")
        try:
            return OsrmRoutingProvider(settings.osrm_base_url, timeout_seconds=settings.osrm_timeout_seconds)
        except ValueError as error:
            raise IntegrationError(code="ROAD_ROUTING_NOT_CONFIGURED", message="OSRM configuration is invalid") from error
    return DeterministicRoutingProvider()


def build_distance_time_matrix(
    locations: Iterable[RoutingLocation],
    *,
    provider: RoutingProvider | None = None,
) -> RoutingMatrix:
    materialized_locations = tuple(locations)
    selected_provider = provider or get_routing_provider()
    return selected_provider.build_matrix(materialized_locations)
