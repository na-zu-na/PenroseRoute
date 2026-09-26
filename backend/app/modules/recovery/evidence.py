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
        risks.append("基础计划或候选状态已变化；该比较只可审计，不再代表可审批候选。")
    unavailable_eta = tuple(
        item for item in orders if item.eta_unavailable_reason is not None
    )
    if unavailable_eta:
        risks.append("部分订单 ETA 不可比较；原因已按订单保存在确定性比较事实中。")
    if comparison.remaining_metrics.reason:
        risks.append(
            "剩余距离与时长不可比较："
            f"{comparison.remaining_metrics.reason}。"
        )
    if handovers:
        risks.append("货物交接尚待现场执行确认。")
    risks.append("候选尚未批准，不能描述为当前执行计划。" if comparison.reviewable else "历史比较仅供审计，不代表当前计划或仍可审批的候选。")
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
    reassigned = "；".join(
        f"{item.order_id}: {item.from_vehicle_id} → {item.to_vehicle_id}"
        for item in evidence.reassigned_orders
    ) or "无"
    eta_parts = []
    for item in evidence.orders:
        if item.eta_delta_seconds is not None:
            eta_parts.append(f"{item.order_id}: {item.eta_delta_seconds:+d} 秒")
        elif item.eta_unavailable_reason:
            eta_parts.append(
                f"{item.order_id}: 不可计算（{item.eta_unavailable_reason}）"
            )
    metrics = evidence.remaining_metrics
    metrics_text = (
        f"剩余距离/时长不可计算（{metrics.reason}）"
        if metrics and metrics.reason
        else (
            "剩余距离变化 "
            f"{metrics.delta_distance_meters} 米，时长变化 "
            f"{metrics.delta_duration_seconds} 秒"
            if metrics else "未提供剩余距离/时长事实"
        )
    )
    structured = RecoveryExplanation(
        summary=(
            "U01 在同一 Base/Candidate 与 Attempt 记录时点生成比较；"
            f"比较记录时点 {evidence.comparison_at}；"
            + ("候选可审核，尚未生效。" if evidence.reviewable else "历史比较仅供审计，不代表当前状态。")
        ),
        impact_explanation=(
            "Completed Freeze 订单："
            f"{', '.join(map(str, evidence.frozen_completed_order_ids)) or '无'}；"
            "Handover 订单："
            f"{', '.join(map(str, evidence.handover_order_ids)) or '无'}。"
        ),
        replanning_explanation=(
            f"订单改派：{reassigned}；"
            "保持不变的订单："
            f"{', '.join(map(str, evidence.unchanged_order_ids)) or '无'}。"
        ),
        result_explanation=(
            "候选未分配订单："
            f"{', '.join(map(str, evidence.unassigned_order_ids)) or '无'}；"
            f"ETA：{'；'.join(eta_parts) or '无可比较订单'}；{metrics_text}。"
        ),
        remaining_risks=evidence.remaining_risks,
    )
    flat = "\n".join((
        structured.summary,
        structured.impact_explanation,
        structured.replanning_explanation,
        structured.result_explanation,
        "剩余风险：" + "；".join(structured.remaining_risks),
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


def comparison_explanation_facts(evidence, comparison=None):
    """One authoritative comparison, materialized before model transport."""
    from app.integrations.agent.contracts import ExplanationFact
    _, sections = explanation_from_plan_comparison(evidence)
    facts = (
        ExplanationFact(id="summary", text=sections.summary),
        ExplanationFact(id="impact", text=sections.impact_explanation),
        ExplanationFact(id="replanning", text=sections.replanning_explanation),
        ExplanationFact(id="result", text=sections.result_explanation),
        ExplanationFact(id="risks", text="剩余风险：" + "；".join(sections.remaining_risks)),
        ExplanationFact(id="review", text=("候选必须经 Dispatcher 人工批准才能生效；当前计划尚未切换。" if evidence.reviewable else "本次历史比较不可用于批准；请查询当前计划和最新审核状态。")),
        ExplanationFact(id="travel_source", text="ETA 基于计划停靠时间；行程采用地理距离与固定车速估算，未接入实时交通。"),
    )
    if comparison is not None:
        facts += (ExplanationFact(id="provenance", text=(
            f"Recovery {comparison.recovery_plan_id}；Base {comparison.base_plan_id}；"
            f"Candidate {comparison.candidate_plan_id}；业务日期 {comparison.business_date}；"
            f"比较时间口径 {comparison.comparison_time_basis}；记录时点 {comparison.comparison_at}。"
        )),)
    return facts
