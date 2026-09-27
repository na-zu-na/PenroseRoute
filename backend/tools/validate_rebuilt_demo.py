"""Read-only consistency check for the isolated rebuilt demo database."""

import json
import os
from collections import Counter
from datetime import date
from math import cos, hypot, radians

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


STAGING_DATABASE = "penrose_route_demo_rebuilt"


def _separation_meters(coordinate, location) -> float:
    latitude = radians((coordinate[1] + float(location.latitude)) / 2)
    return hypot((coordinate[0] - float(location.longitude)) * cos(latitude),
                 coordinate[1] - float(location.latitude)) * 111_195


def validate() -> dict:
    from app.core.config import get_settings

    url = os.environ.get("DEMO_DATABASE_URL")
    if url is None:
        source_url = make_url(get_settings().database_url)
        if source_url.database not in ("penrose_route", STAGING_DATABASE):
            raise ValueError("Backend must point to a PenroseRoute database")
        url = source_url.set(database=STAGING_DATABASE).render_as_string(hide_password=False)
    if not url or make_url(url).database != STAGING_DATABASE:
        raise ValueError(f"DEMO_DATABASE_URL must target {STAGING_DATABASE}")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    from app.db.models import DeliveryPlan, DeliveryPlanOrder, Incident, IncidentAffectedOrder, Location, RecoveryPlan, RouteStop, VehicleRoute
    from app.db.session import engine

    assert engine.url.database == STAGING_DATABASE
    with Session(engine) as session:
        plans = list(session.scalars(select(DeliveryPlan).where(DeliveryPlan.business_date == date(2026, 9, 27))))
        assert Counter(plan.status.value for plan in plans) == {"CURRENT": 1, "CANDIDATE": 1}
        summary = {"database": STAGING_DATABASE, "plans": {}}
        for plan in plans:
            routes = list(session.scalars(select(VehicleRoute).where(VehicleRoute.delivery_plan_id == plan.id)))
            memberships = list(session.scalars(select(DeliveryPlanOrder).where(DeliveryPlanOrder.delivery_plan_id == plan.id)))
            assert plan.total_distance_meters == sum(route.distance_meters for route in routes)
            assert plan.vehicle_count == len(routes)
            assert plan.assigned_order_count + plan.unassigned_order_count == len(memberships)
            assert len({member.order_id for member in memberships}) == len(memberships)
            duration_mismatches = []
            for route in routes:
                stops = list(session.scalars(select(RouteStop).where(RouteStop.vehicle_route_id == route.id)
                                             .order_by(RouteStop.sequence_no)))
                assert [stop.sequence_no for stop in stops] == list(range(1, len(stops) + 1))
                assert route.route_metrics.get("geometry_provider") == "OSRM"
                coordinates = route.route_geometry["coordinates"]
                indices = route.route_metrics["road_leg_end_indices"]
                assert len(coordinates) >= 2 and len(indices) >= len(stops)
                assert _separation_meters(coordinates[0], session.get(Location, route.start_location_id)) <= 250
                for stop, index in zip(stops, indices):
                    assert 0 <= index < len(coordinates)
                    assert _separation_meters(coordinates[index], session.get(Location, stop.location_id)) <= 250
                elapsed = round((route.planned_end_at - route.planned_start_at).total_seconds())
                if abs(elapsed - route.duration_seconds) > 1:
                    duration_mismatches.append({"route_id": str(route.id), "elapsed_seconds": elapsed,
                                                "stored_duration_seconds": route.duration_seconds,
                                                "status": route.status.value,
                                                "preserved_stops": route.route_metrics.get("preserved_stop_count"),
                                                "replanned_stops": route.route_metrics.get("replanned_stop_count")})
            assert not duration_mismatches, f"Route duration mismatches: {duration_mismatches}"
            summary["plans"][plan.status.value] = {
                "id": str(plan.id), "routes": len(routes), "memberships": len(memberships),
                "assigned": plan.assigned_order_count, "unassigned": plan.unassigned_order_count,
                "distance_meters": plan.total_distance_meters,
                "duration_mismatches": duration_mismatches,
            }
        incidents = list(session.scalars(select(Incident).where(Incident.delivery_plan_id ==
                                                    next(plan.id for plan in plans if plan.status.value == "CURRENT"))))
        assert len(incidents) == 3
        merchant_delays = sorted(incident.delay_seconds for incident in incidents
                                 if incident.incident_type.value == "MERCHANT_DELAY")
        assert merchant_delays == [300, 600]
        merchant_incidents = [incident for incident in incidents if incident.incident_type.value == "MERCHANT_DELAY"]
        assert len({incident.merchant_id for incident in merchant_incidents}) == 2
        assert all(incident.status.value == "RESOLVED" for incident in incidents
                   if incident.incident_type.value == "MERCHANT_DELAY")
        assert not list(session.scalars(select(RecoveryPlan).where(
            RecoveryPlan.incident_id.in_([incident.id for incident in merchant_incidents]))))
        vehicle_incident = next(incident for incident in incidents
                                if incident.incident_type.value == "VEHICLE_UNAVAILABLE")
        review = session.scalar(select(RecoveryPlan).where(
            RecoveryPlan.incident_id == vehicle_incident.id,
            RecoveryPlan.candidate_delivery_plan_id.is_not(None)))
        assert review is not None and review.status.value == "PENDING_REVIEW"
        impacts = list(session.scalars(select(IncidentAffectedOrder).where(
            IncidentAffectedOrder.incident_id == vehicle_incident.id)))
        assert {impact.impact_type.value for impact in impacts} >= {"COMPLETED_FROZEN", "HANDOVER_REQUIRED"}
        candidate = next(plan for plan in plans if plan.status.value == "CANDIDATE")
        candidate_stops = list(session.scalars(select(RouteStop).join(VehicleRoute).where(VehicleRoute.delivery_plan_id == candidate.id)))
        frozen_ids = {impact.order_id for impact in impacts if impact.impact_type.value == "COMPLETED_FROZEN"}
        handover_ids = {impact.order_id for impact in impacts if impact.impact_type.value == "HANDOVER_REQUIRED"}
        assert frozen_ids and handover_ids
        assert all(any(stop.order_id == order_id and stop.stop_type.value == "DELIVERY"
                       and stop.status.value == "COMPLETED" for stop in candidate_stops)
                   for order_id in frozen_ids)
        for order_id in handover_ids:
            handover = next(stop for stop in candidate_stops if stop.order_id == order_id
                            and stop.stop_type.value == "HANDOVER"
                            and stop.source_incident_id == vehicle_incident.id)
            delivery = next(stop for stop in candidate_stops if stop.order_id == order_id
                            and stop.stop_type.value == "DELIVERY")
            assert delivery.precedence_stop_id == handover.id
            assert delivery.vehicle_route_id == handover.vehicle_route_id
            assert session.get(VehicleRoute, handover.vehicle_route_id).vehicle_id != vehicle_incident.vehicle_id
        broken_candidate = next(route for route in session.scalars(select(VehicleRoute).where(
            VehicleRoute.delivery_plan_id == candidate.id)) if route.vehicle_id == vehicle_incident.vehicle_id)
        assert broken_candidate.end_location_id == vehicle_incident.incident_location_id
        summary["incident_id"] = str(vehicle_incident.id)
        summary["merchant_delay_seconds"] = merchant_delays
        summary["impact_types"] = sorted({impact.impact_type.value for impact in impacts})
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/delivery-plans/current", params={"business_date": "2026-09-27"})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["id"] == summary["plans"]["CURRENT"]["id"]
        listed = client.get("/api/incidents", params={"business_date": "2026-09-27", "page_size": 100})
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["total"] == 3
    return summary


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
