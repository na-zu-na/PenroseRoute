"""Deterministic full-candidate projection. No LLM or database access here.

The merge follows SqlRecoveryApplication.save_candidate: retained prefixes are
extended on the same vehicle; untouched routes and memberships remain present.
"""
import json
from datetime import datetime
from app.integrations.agent.contracts import RecoveryEvidence


def epoch(value):
    from app.modules.planning.service import timestamp
    return timestamp(datetime.fromisoformat(value))


def project_evidence(input_json, result):
    data, output = json.loads(input_json), json.loads(result.solution_payload_json)
    def signature(stop):
        return (stop["order_id"], stop["stop_type"], stop["location_id"],
                epoch(stop["planned_arrival_at"]), epoch(stop["planned_departure_at"]))

    old = {item["route"]["vehicle_id"]: [signature(s) for s in item["stops"]]
           for item in data["base_routes"]}
    new = {item["route"]["vehicle_id"]: [signature(s) for s in item["stops"]]
           for item in data["preserved_routes"]}
    route_vehicle = {item["route"]["id"]: item["route"]["vehicle_id"] for item in data["base_routes"]}
    old_assignment = {m["order_id"]: route_vehicle.get(m["vehicle_route_id"]) for m in data["base_memberships"]}
    new_assignment = {m["order_id"]: route_vehicle.get(m["vehicle_route_id"]) for m in data["preserved_memberships"]}
    distances = {i["route"]["vehicle_id"]: i["route"]["distance_meters"] for i in data["preserved_routes"]}
    durations = {i["route"]["vehicle_id"]: i["route"]["duration_seconds"] for i in data["preserved_routes"]}
    starts = {i["route"]["vehicle_id"]: epoch(i["route"]["planned_start_at"]) for i in data["preserved_routes"]}
    handovers = []
    for route in output["routes"]:
        vehicle = route["vehicle_id"]
        stops = new.setdefault(vehicle, [])
        for stop in route["stops"]:
            stops.append((stop["order_id"], stop["kind"], data["locations"][stop["location"]]["id"], stop["arrival"], stop["departure"]))
            if stop["kind"] == "DELIVERY":
                new_assignment[stop["order_id"]] = vehicle
            if stop["kind"] == "HANDOVER":
                handovers.append(stop["order_id"])
        distances[vehicle] = distances.get(vehicle, 0) + route["distance_meters"]
        durations[vehicle] = route["end"] - starts.get(vehicle, route["start"])

    def order_signatures(routes):
        signatures = {}
        for vehicle, stops in routes.items():
            for sequence, stop in enumerate(stops, 1):
                signatures.setdefault(stop[0], []).append((vehicle, sequence, *stop[1:]))
        return {key: sorted(value) for key, value in signatures.items()}

    old_orders, new_orders = order_signatures(old), order_signatures(new)
    changed = sorted(key for key in old_assignment if old_assignment[key] != new_assignment.get(key)
                     or old_orders.get(key) != new_orders.get(key))
    reassigned = [{"order_id": key, "from_vehicle_id": old_assignment[key], "to_vehicle_id": new_assignment.get(key)}
                  for key in sorted(old_assignment) if old_assignment[key] != new_assignment.get(key)]
    unassigned = sorted(key for key, value in new_assignment.items() if value is None)
    risks = ["距离、时长为地理估算，未接入道路和实时交通；可行性结论受输入快照与估算精度限制。"]
    if unassigned:
        risks.append("候选仍保留原计划未分配订单：" + ", ".join(unassigned))
    if handovers:
        risks.append("货物交接尚待现场执行确认：" + ", ".join(sorted(handovers)))
    at_risk = [o["id"] for o in data["source_order_facts"] if o["risk_status"] == "AT_RISK" and o["execution_status"] != "COMPLETED"]
    if at_risk:
        risks.append("输入快照中的风险标记尚未代表恢复后已解除：" + ", ".join(sorted(at_risk)))
    risks.append("候选尚未批准，执行前需确认车辆位置、订单状态和交接条件仍与快照一致。")
    base = data["base_plan"]
    return RecoveryEvidence(
        reassigned_orders=tuple(reassigned), changed_order_ids=tuple(changed),
        unchanged_order_ids=tuple(sorted(set(old_assignment)-set(changed))),
        handover_order_ids=tuple(sorted(handovers)),
        changed_vehicle_ids=tuple(sorted(v for v in set(old) | set(new) if old.get(v) != new.get(v))),
        before={key: base[key] for key in ("assigned_order_count", "unassigned_order_count", "vehicle_count", "total_distance_meters", "total_duration_seconds")},
        after={"assigned_order_count": sum(v is not None for v in new_assignment.values()),
               "unassigned_order_count": len(unassigned), "vehicle_count": len(new),
               "total_distance_meters": sum(distances.values()), "total_duration_seconds": sum(durations.values())},
        remaining_risks=tuple(risks))
