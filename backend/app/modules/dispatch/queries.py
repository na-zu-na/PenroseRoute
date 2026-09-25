"""Read-only projections. Sessions are closed before any Agent/model invocation."""
from collections import Counter
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.db.models import DeliveryPlan, Incident, Order, RecoveryPlan, VehicleRoute
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.integrations.agent.contracts import RecoveryError


def iso(value):
    return value.isoformat() if value else None


def plan_projection(plan):
    return {
        "plan_id": str(plan.id), "plan_code": plan.plan_code, "business_date": iso(plan.business_date),
        "version_no": plan.version_no, "status": str(plan.status),
        "validation_status": str(plan.validation_status),
        "matrix_source": (plan.validation_summary or {}).get("matrix_source", "UNSPECIFIED"),
        "assigned_order_count": plan.assigned_order_count,
        "unassigned_order_count": plan.unassigned_order_count,
        "vehicle_count": plan.vehicle_count,
        "total_distance_meters": plan.total_distance_meters,
        "total_duration_seconds": plan.total_duration_seconds,
    }


class DispatchQueries:
    def __init__(self, session_factory, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.clock = session_factory, clock

    def resources(self, business_date: date):
        at = self.clock()
        with self.sessions() as session:
            pairs = FleetRepository(session).get_active_vehicle_driver_pairs(at)
            current = PlanRepository(session).get_current_plan(business_date)
            busy = set(session.scalars(select(VehicleRoute.vehicle_id).where(
                VehicleRoute.delivery_plan_id == current.id,
                VehicleRoute.status.in_(("PLANNED", "ACTIVE")),
            ))) if current else set()
            items = []
            for pair in pairs:
                v, d = pair.vehicle, pair.driver
                available = v.status == "AVAILABLE" and d.status == "AVAILABLE" and v.id not in busy
                items.append({"vehicle_id": str(v.id), "vehicle_code": v.vehicle_code,
                    "driver_id": str(d.id), "driver_code": d.driver_code,
                    "assignment_id": str(pair.id), "capacity_load_units": v.capacity_load_units,
                    "vehicle_status": str(v.status), "driver_status": str(d.status),
                    "location_id": str(v.current_location_id), "location_recorded_at": iso(v.current_location_recorded_at),
                    "idle": available, "on_current_plan": v.id in busy})
            return {"business_date": iso(business_date), "observed_at": iso(at),
                "availability_basis": "有效车人绑定及当前状态，不代表未来班次或剩余载重",
                "total": len(items), "idle_count": sum(x["idle"] for x in items),
                "items": items[:100], "truncated": len(items) > 100}

    def operations(self, business_date: date):
        now = self.clock()
        with self.sessions() as session:
            orders = list(session.scalars(select(Order).where(Order.business_date == business_date).order_by(Order.order_code)))
            plan = PlanRepository(session).get_current_plan(business_date)
            incidents = list(session.scalars(select(Incident).where(Incident.delivery_plan_id == plan.id,
                Incident.status != "RESOLVED").order_by(Incident.detected_at))) if plan else []
            risks = []
            for order in orders:
                if order.execution_status == "COMPLETED":
                    continue
                # Persisted risk and overdue deadline only; do not invent live traffic ETA.
                deadline = order.delivery_window_end_at
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if order.risk_status == "AT_RISK" or deadline < now:
                    risks.append({"order_id": str(order.id), "order_code": order.order_code,
                        "execution_status": str(order.execution_status), "deadline": iso(deadline),
                        "reason": "DEADLINE_PASSED" if deadline < now else "RECORDED_AT_RISK"})
            return {"business_date": iso(business_date), "observed_at": iso(now),
                "risk_basis": "已记录风险与未完成订单的截止时间；不含实时交通预测",
                "current_plan": plan_projection(plan) if plan else None,
                "order_count": len(orders), "execution_counts": dict(Counter(str(o.execution_status) for o in orders)),
                "at_risk_count": len(risks), "at_risk_orders": risks[:100],
                "open_incident_count": len(incidents), "open_incidents": [
                    {"incident_id": str(i.id), "incident_type": str(i.incident_type), "status": str(i.status),
                     "vehicle_id": str(i.vehicle_id) if i.vehicle_id else None,
                     "delay_seconds": i.delay_seconds} for i in incidents[:100]],
                "truncated": len(risks) > 100 or len(incidents) > 100}

    def proposal(self, recovery_id: UUID):
        with self.sessions() as session:
            recovery = session.get(RecoveryPlan, recovery_id)
            if recovery is None:
                raise RecoveryError("RECOVERY_NOT_FOUND", "恢复尝试不存在", 404)
            base = session.get(DeliveryPlan, recovery.base_delivery_plan_id)
            candidate = session.get(DeliveryPlan, recovery.candidate_delivery_plan_id) if recovery.candidate_delivery_plan_id else None
            return {"recovery_plan_id": str(recovery.id), "incident_id": str(recovery.incident_id),
                "status": str(recovery.status), "attempt_no": recovery.attempt_no,
                "scope": str(recovery.replanning_scope), "solver_status": recovery.solver_status,
                "validation_status": recovery.validation_status, "explanation": recovery.agent_explanation,
                "structured_explanation": (recovery.solver_validation_summary or {}).get("explanation"),
                "explanation_source": (recovery.solver_validation_summary or {}).get("explanation_source"),
                "recovery_evidence": ((recovery.solver_validation_summary or {}).get("validation") or {}).get("recovery_evidence"),
                "base_plan": plan_projection(base), "candidate_plan": plan_projection(candidate) if candidate else None,
                "decision": recovery.dispatcher_decision,
                "requires_human_review": recovery.status == "PENDING_REVIEW"}

    def compare(self, base_id: UUID, candidate_id: UUID):
        with self.sessions() as session:
            repo = PlanRepository(session)
            base, candidate = repo.get_plan_with_routes(base_id), repo.get_plan_with_routes(candidate_id)
            if base is None or candidate is None:
                raise RecoveryError("PLAN_NOT_FOUND", "比较计划不存在", 404)
            if base.id == candidate.id or base.business_date != candidate.business_date or base.plan_group_id != candidate.plan_group_id:
                raise RecoveryError("PLAN_COMPARISON_INVALID", "请选择同一计划组的不同版本", 422)
            def assignments(plan):
                vehicles = {r.id: str(r.vehicle_id) for r in plan.routes}
                return {str(o.order_id): {"status": str(o.assignment_status),
                    "vehicle_id": vehicles.get(o.vehicle_route_id)} for o in plan.plan_orders}
            old, new = assignments(base), assignments(candidate)
            changed = [{"order_id": key, "before": old.get(key), "after": new.get(key)}
                       for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
            def routes(plan):
                return {str(r.vehicle_id): [(str(s.order_id), str(s.stop_type), iso(s.planned_arrival_at))
                    for s in r.stops] for r in plan.routes}
            a, b = routes(base), routes(candidate)
            changed_vehicles = [key for key in sorted(a.keys() | b.keys()) if a.get(key) != b.get(key)]
            metrics = ("assigned_order_count", "unassigned_order_count", "total_distance_meters", "total_duration_seconds")
            return {"base_plan": plan_projection(base), "candidate_plan": plan_projection(candidate),
                "delta": {key: getattr(candidate, key) - getattr(base, key)
                          if getattr(base, key) is not None and getattr(candidate, key) is not None else None for key in metrics},
                "changed_order_count": len(changed), "changed_orders": changed[:100],
                "changed_vehicle_count": len(changed_vehicles), "changed_vehicle_ids": changed_vehicles[:100],
                "truncated": len(changed) > 100 or len(changed_vehicles) > 100}
