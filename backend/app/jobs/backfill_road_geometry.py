"""Replace legacy plan lines with OSRM geometry without recalculating plan facts."""

import argparse
import json
from dataclasses import dataclass
from datetime import date
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import IntegrationError
from app.db.models import DeliveryPlan, VehicleRoute
from app.db.models.planning import DeliveryPlanStatus
from app.db.session import SessionLocal
from app.integrations.routing.contracts import RoutingLocation
from app.integrations.routing.distance_matrix import get_routing_provider


ELIGIBLE_STATUSES = (DeliveryPlanStatus.CURRENT, DeliveryPlanStatus.CANDIDATE)


@dataclass(frozen=True)
class BackfillResult:
    scanned_count: int
    updated_count: int
    skipped_count: int
    failures: tuple[tuple[str, str], ...]

    @property
    def failed_count(self) -> int:
        return len(self.failures)


def _route_waypoints(route: VehicleRoute) -> tuple[tuple[RoutingLocation, ...], tuple]:
    stops = sorted(route.stops, key=lambda stop: stop.sequence_no)
    locations = [route.start_location, *(stop.location for stop in stops)]
    if not stops or route.end_location_id != stops[-1].location_id:
        locations.append(route.end_location)
    waypoints = tuple(
        RoutingLocation(location.id, float(location.latitude), float(location.longitude))
        for location in locations
    )
    stop_order = tuple((stop.id, stop.sequence_no, stop.location_id) for stop in stops)
    return waypoints, stop_order


def backfill_road_geometry(
    business_date: date,
    *,
    force: bool = False,
    session_factory: Callable[[], Session] = SessionLocal,
    routing_provider=None,
) -> BackfillResult:
    provider = routing_provider or get_routing_provider()
    build_route = getattr(provider, "build_route", None)
    if build_route is None:
        raise IntegrationError(code="ROAD_ROUTING_NOT_CONFIGURED", message="Enable ROUTING_PROVIDER=osrm to backfill routes")

    with session_factory() as session:
        route_ids = session.scalars(
            select(VehicleRoute.id).join(DeliveryPlan).where(
                DeliveryPlan.business_date == business_date,
                DeliveryPlan.status.in_(ELIGIBLE_STATUSES),
            ).order_by(DeliveryPlan.version_no, VehicleRoute.route_no)
        ).all()

    updated = skipped = 0
    failures = []
    for route_id in route_ids:
        with session_factory() as session:
            route = session.get(VehicleRoute, route_id)
            if route is None or route.delivery_plan.status not in ELIGIBLE_STATUSES:
                skipped += 1
                continue
            if route.route_geometry is not None and not force:
                skipped += 1
                continue
            waypoints, stop_order = _route_waypoints(route)
            old_geometry, old_metrics = route.route_geometry, route.route_metrics

        try:
            road = build_route(waypoints)  # No database transaction is open during the HTTP request.
            if len(road.leg_end_indices) != len(waypoints) - 1:
                raise IntegrationError(code="ROAD_GEOMETRY_INVALID", message="OSRM leg count does not match stops")
            with session_factory.begin() as session:
                route = session.scalar(select(VehicleRoute).where(VehicleRoute.id == route_id).with_for_update())
                if (
                    route is None
                    or route.delivery_plan.business_date != business_date
                    or route.delivery_plan.status not in ELIGIBLE_STATUSES
                    or _route_waypoints(route) != (waypoints, stop_order)
                    or route.route_geometry != old_geometry
                    or route.route_metrics != old_metrics
                ):
                    raise IntegrationError(code="ROAD_BACKFILL_FACTS_CHANGED", message="Route changed during road lookup")
                route.route_geometry = road.geometry
                route.route_metrics = {
                    **(route.route_metrics or {}),
                    "geometry_provider": "OSRM",
                    "road_leg_end_indices": list(road.leg_end_indices),
                }
            updated += 1
        except IntegrationError as error:
            failures.append((str(route_id), error.code))

    return BackfillResult(len(route_ids), updated, skipped, tuple(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-date", required=True, type=date.fromisoformat)
    parser.add_argument("--force", action="store_true", help="Replace stored legacy or OSRM geometry")
    args = parser.parse_args()
    result = backfill_road_geometry(args.business_date, force=args.force)
    print(json.dumps({
        "business_date": args.business_date.isoformat(),
        "scanned": result.scanned_count,
        "updated": result.updated_count,
        "skipped": result.skipped_count,
        "failures": [{"route_id": route_id, "code": code} for route_id, code in result.failures],
    }))
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
