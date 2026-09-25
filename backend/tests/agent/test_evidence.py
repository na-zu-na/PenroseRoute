"""Independent examples for the business projection consumed by the Agent."""
import json
from datetime import datetime, timezone
from uuid import uuid4
from app.integrations.agent.contracts import SolverResult
from app.modules.recovery.evidence import project_evidence


def iso(second):
    return datetime.fromtimestamp(second, timezone.utc).isoformat()


def test_full_candidate_keeps_untouched_and_unassigned_orders():
    order, retained, unassigned, old_vehicle, new_vehicle, retained_vehicle, location = [str(uuid4()) for _ in range(7)]
    def route(vehicle, order_id, meters, end):
        return {"route": {"id": str(uuid4()), "vehicle_id": vehicle, "distance_meters": meters,
                "duration_seconds": end-100, "planned_start_at": iso(100)},
            "stops": [{"order_id": order_id, "stop_type": "DELIVERY", "location_id": location,
                "planned_arrival_at": iso(end-10), "planned_departure_at": iso(end)}]}
    original, untouched = route(old_vehicle, order, 200, 200), route(retained_vehicle, retained, 300, 300)
    memberships = [{"order_id": order, "vehicle_route_id": original["route"]["id"]},
        {"order_id": retained, "vehicle_route_id": untouched["route"]["id"]},
        {"order_id": unassigned, "vehicle_route_id": None}]
    data = {"base_routes": [original, untouched], "preserved_routes": [untouched],
        "base_memberships": memberships, "preserved_memberships": memberships[1:],
        "locations": [{"id": location}], "source_order_facts": [],
        "base_plan": {"assigned_order_count": 2, "unassigned_order_count": 1, "vehicle_count": 2,
            "total_distance_meters": 500, "total_duration_seconds": 300}}
    output = {"routes": [{"vehicle_id": new_vehicle, "start": 400, "end": 600, "distance_meters": 400,
        "stops": [{"order_id": order, "kind": "DELIVERY", "location": 0, "arrival": 590, "departure": 600}]}]}
    evidence = project_evidence(json.dumps(data), SolverResult(status="FEASIBLE", solution_payload_json=json.dumps(output)))
    assert set(map(str, evidence.unchanged_order_ids)) == {retained, unassigned}
    assert list(map(str, evidence.changed_order_ids)) == [order]
    assert evidence.reassigned_orders[0].model_dump(mode="json") == {"order_id": order, "from_vehicle_id": old_vehicle, "to_vehicle_id": new_vehicle}
    assert set(map(str, evidence.changed_vehicle_ids)) == {old_vehicle, new_vehicle}
    assert evidence.after.model_dump() == {"assigned_order_count": 2, "unassigned_order_count": 1, "vehicle_count": 2,
        "total_distance_meters": 700, "total_duration_seconds": 400}
    assert any(unassigned in risk for risk in evidence.remaining_risks)


def test_retained_prefix_and_new_route_are_counted_as_one_vehicle():
    vehicle, route_id, order, location = [str(uuid4()) for _ in range(4)]
    prefix = {"route": {"id": route_id, "vehicle_id": vehicle, "distance_meters": 100,
        "duration_seconds": 50, "planned_start_at": iso(100)}, "stops": []}
    base = {"assigned_order_count": 1, "unassigned_order_count": 0, "vehicle_count": 1,
        "total_distance_meters": 250, "total_duration_seconds": 200}
    data = {"base_routes": [prefix], "preserved_routes": [prefix],
        "base_memberships": [{"order_id": order, "vehicle_route_id": route_id}], "preserved_memberships": [],
        "locations": [{"id": location}], "source_order_facts": [], "base_plan": base}
    output = {"routes": [{"vehicle_id": vehicle, "start": 400, "end": 600, "distance_meters": 200,
        "stops": [{"order_id": order, "kind": "DELIVERY", "location": 0, "arrival": 590, "departure": 600}]}]}
    evidence = project_evidence(json.dumps(data), SolverResult(status="FEASIBLE", solution_payload_json=json.dumps(output)))
    assert evidence.after.vehicle_count == 1
    assert evidence.after.total_distance_meters == 300
    # Duration includes the interval from the retained route's original start.
    assert evidence.after.total_duration_seconds == 500
    assert not evidence.reassigned_orders and evidence.changed_order_ids
