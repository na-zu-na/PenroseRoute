"""Deterministic full-candidate projection. No LLM or database access here.

The merge follows SqlRecoveryApplication.save_candidate: retained prefixes are
extended on the same vehicle; untouched routes and memberships remain present.
"""
import json
from datetime import datetime
from app.integrations.agent.contracts import RecoveryEvidence


def project_formal_evidence(context, outcome):
    """Compare the formal candidate projection using only materialized facts."""
    route_by_id = {route.id: route for route in context.routes}
    before_assignment = {
        item.order_id: (
            route_by_id[item.vehicle_route_id].vehicle_id
            if item.vehicle_route_id is not None else None
        )
        for item in context.plan_orders
    }
    after_assignment = before_assignment.copy()
    target_ids = {item.order_id for item in context.target_orders}
    for order_id in target_ids:
        after_assignment[order_id] = None
    solver_routes = {route.vehicle_id: route for route in outcome.solver_result.routes}
    for route in outcome.solver_result.routes:
        for stop in route.stops:
            if stop.stop_type.value == "DELIVERY":
                after_assignment[stop.order_id] = route.vehicle_id

    rebuilt_route_ids = {
        item.original_route_id for item in context.target_orders
        if item.original_route_id is not None
    }
    after_distances = []
    after_durations = []
    remaining_solver_routes = solver_routes.copy()
    for base_route in context.routes:
        solved = remaining_solver_routes.pop(base_route.vehicle_id, None)
        preserved = (
            base_route.stops if base_route.id not in rebuilt_route_ids
            else tuple(stop for stop in base_route.stops if stop.order_id not in target_ids or stop.status == "COMPLETED")
        )
        if not preserved and solved is None:
            continue
        after_distances.append(solved.distance_meters if solved else base_route.distance_meters)
        after_durations.append(solved.duration_seconds if solved else base_route.duration_seconds)
    after_distances.extend(route.distance_meters for route in remaining_solver_routes.values())
    after_durations.extend(route.duration_seconds for route in remaining_solver_routes.values())

    reassigned = tuple(
        {"order_id": order_id, "from_vehicle_id": before_assignment[order_id],
         "to_vehicle_id": after_assignment[order_id]}
        for order_id in sorted(before_assignment)
        if before_assignment[order_id] != after_assignment[order_id]
    )
    handovers = tuple(sorted(
        item.order_id for item in context.target_orders if item.handover_required
    ))
    risks = ["距离和行驶时间为地理估算，未接入实时道路交通。"]
    if handovers:
        risks.append("货物交接尚待现场执行确认。")
    risks.append("候选尚未批准；执行前需确认位置和订单状态仍与快照一致。")
    return RecoveryEvidence(
        reassigned_orders=reassigned,
        unchanged_order_ids=tuple(sorted(set(before_assignment) - target_ids)),
        changed_order_ids=tuple(sorted(target_ids)),
        handover_order_ids=handovers,
        changed_vehicle_ids=tuple(sorted(
            {route.vehicle_id for route in outcome.solver_result.routes}
            | {route_by_id[route_id].vehicle_id for route_id in rebuilt_route_ids}
        )),
        before={
            "assigned_order_count": sum(value is not None for value in before_assignment.values()),
            "unassigned_order_count": sum(value is None for value in before_assignment.values()),
            "vehicle_count": len(context.routes),
            "total_distance_meters": sum(route.distance_meters for route in context.routes),
            "total_duration_seconds": sum(route.duration_seconds for route in context.routes),
        },
        after={
            "assigned_order_count": sum(value is not None for value in after_assignment.values()),
            "unassigned_order_count": sum(value is None for value in after_assignment.values()),
            "vehicle_count": len(after_distances),
            "total_distance_meters": sum(after_distances),
            "total_duration_seconds": sum(after_durations),
        },
        remaining_risks=tuple(risks),
    )


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
