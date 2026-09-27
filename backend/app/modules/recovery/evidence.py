"""Deterministic evidence adapters; no LLM or database access here.

The formal P1 path adapts the canonical U01 comparison. ``project_evidence``
remains for the legacy in-memory workflow and is not used by the formal API.
"""
import json
from datetime import datetime
from app.integrations.agent.contracts import (
    ComparisonOrderFact,
    RecoveryEvidence,
    RecoveryExplanation,
    RemainingMetricsFact,
)


def evidence_from_plan_comparison(comparison):
    """Adapt the canonical U01 comparison; never recalculate its semantics."""
    orders = tuple(
        ComparisonOrderFact(
            order_id=item.order_id,
            base_assignment_status=item.base_assignment_status,
            candidate_assignment_status=item.candidate_assignment_status,
            base_vehicle_id=item.base_vehicle_id,
            candidate_vehicle_id=item.candidate_vehicle_id,
            assignment_changed=item.assignment_changed,
            route_task_changed=item.route_task_changed,
            base_delivery_eta=item.base_delivery_eta,
            candidate_delivery_eta=item.candidate_delivery_eta,
            eta_delta_seconds=item.eta_delta_seconds,
            eta_basis=item.eta_basis,
            eta_unavailable_reason=item.eta_unavailable_reason,
        )
        for item in comparison.orders
    )
    reassigned = tuple(
        {
            "order_id": item.order_id,
            "from_vehicle_id": item.base_vehicle_id,
            "to_vehicle_id": item.candidate_vehicle_id,
        }
        for item in comparison.orders
        if item.base_vehicle_id is not None
        and item.candidate_vehicle_id is not None
        and item.base_vehicle_id != item.candidate_vehicle_id
    )
    changed = tuple(sorted(
        item.order_id for item in comparison.orders
        if item.assignment_changed or item.route_task_changed
    ))
    unchanged = tuple(sorted(
        item.order_id for item in comparison.orders
        if not item.assignment_changed and not item.route_task_changed
    ))
    unassigned = tuple(sorted(
        item.order_id for item in comparison.orders
        if item.candidate_assignment_status == "UNASSIGNED"
    ))
    handovers = tuple(sorted({
        item.order_id for item in comparison.stop_changes
        if item.stop_type == "HANDOVER" and item.change_type != "REMOVED"
    }))
    risks = []
    if not comparison.reviewable:
        risks.append("The Base Plan or Candidate status changed; this comparison is audit-only and is no longer reviewable.")
    unavailable_eta = tuple(
        item for item in orders if item.eta_unavailable_reason is not None
    )
    if unavailable_eta:
        risks.append("Some order ETAs are not comparable; the deterministic comparison records each reason.")
    if comparison.remaining_metrics.reason:
        risks.append(
            "Remaining distance and duration are not comparable: "
            f"{comparison.remaining_metrics.reason}."
        )
    if handovers:
        risks.append("Cargo handover still requires confirmation during execution.")
    risks.append("The Candidate is not approved and is not the Current Plan." if comparison.reviewable else "This historical comparison is audit-only; it is neither the Current Plan nor a reviewable Candidate.")
    return RecoveryEvidence(
        source="P1_PLAN_COMPARISON",
        comparison_at=comparison.comparison_at,
        comparison_time_basis=comparison.comparison_time_basis,
        reviewable=comparison.reviewable,
        reassigned_orders=reassigned,
        unchanged_order_ids=unchanged,
        changed_order_ids=changed,
        unassigned_order_ids=unassigned,
        handover_order_ids=handovers,
        frozen_completed_order_ids=comparison.frozen_completed_order_ids,
        orders=orders,
        before={
            "assigned_order_count": sum(
                item.base_assignment_status == "ASSIGNED" for item in orders
            ),
            "unassigned_order_count": sum(
                item.base_assignment_status == "UNASSIGNED" for item in orders
            ),
        },
        after={
            "assigned_order_count": sum(
                item.candidate_assignment_status == "ASSIGNED" for item in orders
            ),
            "unassigned_order_count": sum(
                item.candidate_assignment_status == "UNASSIGNED" for item in orders
            ),
        },
        changed_vehicle_ids=comparison.affected_vehicle_ids,
        remaining_metrics=RemainingMetricsFact.model_validate(
            {
                name: getattr(comparison.remaining_metrics, name)
                for name in RemainingMetricsFact.model_fields
            }
        ),
        remaining_risks=tuple(risks),
    )


def explanation_from_plan_comparison(evidence: RecoveryEvidence):
    """Render canonical comparison facts without asking a model to fill gaps."""
    reassigned = "; ".join(
        f"{item.order_id}: {item.from_vehicle_id} → {item.to_vehicle_id}"
        for item in evidence.reassigned_orders
    ) or "none"
    eta_parts = []
    for item in evidence.orders:
        if item.eta_delta_seconds is not None:
            eta_parts.append(f"{item.order_id}: {item.eta_delta_seconds:+d} seconds")
        elif item.eta_unavailable_reason:
            eta_parts.append(
                f"{item.order_id}: unavailable ({item.eta_unavailable_reason})"
            )
    metrics = evidence.remaining_metrics
    metrics_text = (
        f"Remaining distance/duration unavailable ({metrics.reason})"
        if metrics and metrics.reason
        else (
            "Remaining distance change "
            f"{metrics.delta_distance_meters} meters; duration change "
            f"{metrics.delta_duration_seconds} seconds"
            if metrics else "No remaining distance/duration facts were provided"
        )
    )
    structured = RecoveryExplanation(
        summary=(
            "U01 compared the same Base Plan and Candidate at the recorded Attempt time; "
            f"comparison recorded at {evidence.comparison_at}. "
            + ("The Candidate is reviewable but not active." if evidence.reviewable else "This historical comparison is audit-only and does not represent the current state.")
        ),
        impact_explanation=(
            "Frozen completed orders: "
            f"{', '.join(map(str, evidence.frozen_completed_order_ids)) or 'none'}; "
            "handover orders: "
            f"{', '.join(map(str, evidence.handover_order_ids)) or 'none'}."
        ),
        replanning_explanation=(
            f"Reassigned orders: {reassigned}; "
            "unchanged orders: "
            f"{', '.join(map(str, evidence.unchanged_order_ids)) or 'none'}."
        ),
        result_explanation=(
            "Unassigned Candidate orders: "
            f"{', '.join(map(str, evidence.unassigned_order_ids)) or 'none'}; "
            f"ETA: {'; '.join(eta_parts) or 'no comparable orders'}; {metrics_text}."
        ),
        remaining_risks=evidence.remaining_risks,
    )
    flat = "\n".join((
        structured.summary,
        structured.impact_explanation,
        structured.replanning_explanation,
        structured.result_explanation,
        "Remaining risks: " + "; ".join(structured.remaining_risks),
    ))
    return flat, structured


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
    risks = ["Distance and duration are geographic estimates without road routing or live traffic; feasibility depends on the input snapshot and estimation accuracy."]
    if unassigned:
        risks.append("The Candidate still contains unassigned Base Plan orders: " + ", ".join(unassigned))
    if handovers:
        risks.append("Cargo handover still requires confirmation during execution: " + ", ".join(sorted(handovers)))
    at_risk = [o["id"] for o in data["source_order_facts"] if o["risk_status"] == "AT_RISK" and o["execution_status"] != "COMPLETED"]
    if at_risk:
        risks.append("Risk flags in the input snapshot do not prove recovery resolved them: " + ", ".join(sorted(at_risk)))
    risks.append("The Candidate is not approved; confirm vehicle locations, order status, and handover conditions before execution.")
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


def comparison_explanation_facts(evidence, comparison=None):
    """One authoritative comparison, materialized before model transport."""
    from app.integrations.agent.contracts import ExplanationFact
    _, sections = explanation_from_plan_comparison(evidence)
    facts = (
        ExplanationFact(id="summary", text=sections.summary),
        ExplanationFact(id="impact", text=sections.impact_explanation),
        ExplanationFact(id="replanning", text=sections.replanning_explanation),
        ExplanationFact(id="result", text=sections.result_explanation),
        ExplanationFact(id="risks", text="Remaining risks: " + "; ".join(sections.remaining_risks)),
        ExplanationFact(id="review", text=("The Candidate requires Dispatcher approval before activation; the Current Plan has not changed." if evidence.reviewable else "This historical comparison cannot be approved; check the Current Plan and latest review status.")),
        ExplanationFact(id="travel_source", text="ETAs use planned stop times; travel uses geographic distance and fixed-speed estimates, not live traffic."),
    )
    if comparison is not None:
        facts += (ExplanationFact(id="provenance", text=(
            f"Recovery {comparison.recovery_plan_id}; Base {comparison.base_plan_id}; "
            f"Candidate {comparison.candidate_plan_id}; business date {comparison.business_date}; "
            f"comparison time basis {comparison.comparison_time_basis}; recorded at {comparison.comparison_at}."
        )),)
    return facts
