"""Rebuild the isolated September 27 demo through the public P0 API.

The live database is never modified by this command. Set DEMO_DATABASE_URL to
the dedicated staging database URL before invoking it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch


BUSINESS_DATE = "2026-09-27"
STAGING_DATABASE = "penrose_route_demo_rebuilt"


def choose_breakdown_prefix(routes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pick a real route prefix with both delivered and still-onboard cargo."""
    for route in routes:
        onboard: set[str] = set()
        completed: set[str] = set()
        prefix: list[dict[str, Any]] = []
        for stop in route["stops"]:
            prefix.append(stop)
            order_id = stop.get("order_id")
            if stop["stop_type"] == "PICKUP" and order_id:
                onboard.add(order_id)
            elif stop["stop_type"] == "DELIVERY" and order_id:
                onboard.discard(order_id)
                completed.add(order_id)
            if completed and onboard:
                return route, prefix
    raise ValueError("No route has both completed and onboard orders")


def _data(response, expected_status: int = 200) -> dict[str, Any] | list[dict[str, Any]]:
    body = response.json()
    if response.status_code != expected_status or not body.get("success"):
        raise RuntimeError(f"{response.request.method} {response.request.url.path}: "
                           f"HTTP {response.status_code}, {body.get('code')}: {body.get('message')}")
    return body["data"]


def _run() -> None:
    from sqlalchemy.engine import make_url
    from app.core.config import get_settings

    database_url = os.environ.get("DEMO_DATABASE_URL")
    if database_url is None:
        source_url = make_url(get_settings().database_url)
        if source_url.database != "penrose_route":
            raise ValueError("Without DEMO_DATABASE_URL, backend must point to penrose_route")
        database_url = source_url.set(database=STAGING_DATABASE).render_as_string(hide_password=False)
    if not database_url or make_url(database_url).database != STAGING_DATABASE:
        raise ValueError(f"DEMO_DATABASE_URL must target {STAGING_DATABASE}")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    os.environ["ROUTING_PROVIDER"] = "osrm"
    os.environ["RECOVERY_ORCHESTRATION_MODE"] = "deterministic"
    token = "demo-rebuild-local-token-000000000000"
    os.environ["DISPATCH_API_TOKENS"] = json.dumps({token: {
        "role": "dispatcher", "subject": "demo-rebuild",
    }})

    from fastapi.testclient import TestClient
    from app.main import app
    from app.db.session import engine

    if engine.url.database != STAGING_DATABASE:
        raise RuntimeError("Application engine is not connected to the staging database")
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(app, headers=auth) as client:
        generated = _data(client.post("/api/planning/generate", json={"business_date": BUSINESS_DATE}), 201)
        base_id = generated["delivery_plan_id"]
        merchants = {item["merchant_code"]: item for item in
                     _data(client.get("/api/merchants", params={"page_size": 100}))["items"]}
        merchant_incidents = []
        for merchant_code, delay_seconds in (("MER-001", 300), ("MER-002", 600)):
            merchant = merchants[merchant_code]
            original_ready = datetime.fromisoformat(merchant["operational_ready_at"])
            delay = _data(client.post("/api/incidents/merchant-delay", json={
                "business_date": BUSINESS_DATE,
                "merchant_id": merchant["id"],
                "updated_ready_at": (original_ready + timedelta(seconds=delay_seconds)).isoformat(),
                "detected_at": (original_ready - timedelta(minutes=10)).isoformat(),
            }), 201)
            if (delay["status"] != "RESOLVED" or delay["requires_replanning"]
                    or delay["delay_seconds"] != delay_seconds):
                raise AssertionError(f"Short merchant delay did not resolve: {merchant_code}")
            merchant_incidents.append(delay["incident_id"])
        routes = _data(client.get(f"/api/delivery-plans/{base_id}/routes"))
        for route in routes:
            route["stops"] = _data(client.get(f"/api/vehicle-routes/{route['id']}/stops"))
        route, prefix = choose_breakdown_prefix(routes)
        previous = datetime.fromisoformat(prefix[0]["planned_arrival_at"]) - timedelta(seconds=1)
        for stop in prefix:
            arrival = max(previous + timedelta(seconds=1), datetime.fromisoformat(stop["planned_arrival_at"]))
            for action, event in (
                ("arrive", arrival),
                ("start-service", arrival + timedelta(seconds=1)),
                ("complete", max(arrival + timedelta(seconds=2), datetime.fromisoformat(stop["planned_departure_at"]))),
            ):
                _data(client.post(f"/api/route-stops/{stop['id']}/{action}", json={"occurred_at": event.isoformat()}))
            previous = max(arrival + timedelta(seconds=2), datetime.fromisoformat(stop["planned_departure_at"]))

        incident_at = previous + timedelta(minutes=1)
        incident = _data(client.post("/api/incidents/vehicle-unavailable", json={
            "business_date": BUSINESS_DATE, "vehicle_id": route["vehicle_id"],
            "detected_at": incident_at.isoformat(),
        }), 201)
        incident_id = incident["incident_id"]
        affected = _data(client.get(f"/api/incidents/{incident_id}/affected-orders"))
        types = {item["impact_type"] for item in affected}
        if not {"COMPLETED_FROZEN", "HANDOVER_REQUIRED"} <= types:
            raise AssertionError(f"Incomplete breakdown impact: {types}")
        with patch("app.modules.recovery.deterministic_context._now", return_value=incident_at):
            recovery = _data(client.post(f"/api/incidents/{incident_id}/recovery", json={}), 201)
        if recovery["outcome"] != "PENDING_REVIEW":
            raise AssertionError(f"Recovery did not create a reviewable candidate: {recovery['outcome']}")
        candidate_id = recovery["candidate_delivery_plan_id"]
        if _data(client.get(f"/api/delivery-plans/{base_id}"))["status"] != "CURRENT":
            raise AssertionError("Base plan changed before approval")
        if _data(client.get(f"/api/delivery-plans/{candidate_id}"))["status"] != "CANDIDATE":
            raise AssertionError("Candidate was applied before approval")
        print(json.dumps({
            "database": STAGING_DATABASE, "business_date": BUSINESS_DATE,
            "base_plan_id": base_id, "base_routes": len(routes),
            "breakdown_route_id": route["id"], "executed_stops": len(prefix),
            "incident_id": incident_id, "affected_impacts": sorted(types),
            "resolved_merchant_incident_ids": merchant_incidents,
            "recovery_outcome": recovery["outcome"], "candidate_plan_id": candidate_id,
            "base_status": "CURRENT", "candidate_status": "CANDIDATE",
        }, indent=2))


if __name__ == "__main__":
    _run()
