"""Read-only projections. Sessions are closed before any Agent/model invocation."""
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.db.models import VehicleRoute
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository


def iso(value):
    return value.isoformat() if value else None



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
            return {"business_date": iso(business_date), "as_of": iso(at), "observed_at": iso(at),
                "missing_reasons": [], "facts": [{"id": "resources", "text": f"资源读取时点 {at.isoformat()}：有效车人绑定 {len(items)} 组，空闲 {sum(x['idle'] for x in items)} 组；依据当前状态，不代表未来班次或剩余载重。"}],
                "availability_basis": "有效车人绑定及当前状态，不代表未来班次或剩余载重",
                "total": len(items), "idle_count": sum(x["idle"] for x in items),
                "items": items[:100], "truncated": len(items) > 100}

    def operations(self, business_date: date):
        from dataclasses import asdict
        from fastapi.encoders import jsonable_encoder
        from app.core.errors import NotFound
        from app.modules.operations.queries import OperationsQueryService
        with self.sessions() as session:
            try:
                data = jsonable_encoder(asdict(OperationsQueryService(session).dashboard(business_date)))
            except NotFound as error:
                return self._envelope({"current_plan": None}, business_date=business_date,
                    missing=[error.code, "ALERT_QUERY_UNAVAILABLE"], facts=[
                        {"id": "current_plan", "text": "所选日期没有 Current Plan；无法提供当前运营摘要。"},
                        {"id": "alerts", "text": "提醒数据不可用：U06 正式只读查询服务尚未接入。"},
                    ])
        plan = data["current_plan"]
        data["unfinished_order_count"] = data["orders"]["total"] - data["orders"]["completed"]
        data.update(active_alert_count=None, alert_reason_counts=None, alerts_as_of=None)
        return self._envelope(data, business_date=business_date, missing=["ALERT_QUERY_UNAVAILABLE"], facts=[
            {"id": "current_plan", "text": f"业务日期 {business_date}，Current Plan {plan['delivery_plan_id']}，版本 {plan['version_no']}。", "plan_id": plan["delivery_plan_id"]},
            {"id": "operations", "text": f"运营计算时点 {data['calculated_at']}：未完成订单 {data['unfinished_order_count']}，运营快照 AT_RISK {data['orders']['at_risk']}。", "plan_id": plan["delivery_plan_id"]},
            {"id": "reviews", "text": f"未解决 Incident {data['open_incidents']}，待审核 Candidate {data['pending_recovery_reviews']}；候选尚未生效。", "plan_id": plan["delivery_plan_id"]},
            {"id": "alerts", "text": "持久化活动提醒数及原因计数不可用：U06 正式只读查询服务尚未接入；运营风险数不代表活动提醒数。"},
        ])

    def proposal(self, recovery_id: UUID):
        from fastapi.encoders import jsonable_encoder
        from app.modules.recovery.queries import RecoveryQueryService
        with self.sessions() as session:
            data = jsonable_encoder(RecoveryQueryService(session).get_attempt(recovery_id))
        return self._envelope(data, facts=[{
            "id": "recovery", "recovery_plan_id": str(recovery_id),
            "plan_id": data["candidate_delivery_plan_id"],
            "text": f"Recovery {recovery_id}：状态 {data['status']}，范围 {data['replanning_scope']}，审批决定 {data['dispatcher_decision']}。历史解释仅对应生成时点，不能代表当前可审批状态。",
        }])

    def compare(self, recovery_id: UUID):
        from dataclasses import asdict
        from fastapi.encoders import jsonable_encoder
        from app.modules.planning.comparison import PlanComparisonService
        from app.modules.recovery.evidence import evidence_from_plan_comparison, comparison_explanation_facts
        with self.sessions() as session:
            comparison = PlanComparisonService(session).compare_recovery(recovery_id)
            data = jsonable_encoder(asdict(comparison))
            facts = comparison_explanation_facts(evidence_from_plan_comparison(comparison), comparison)
        return self._envelope(data, business_date=comparison.business_date, facts=[{
            "id": fact.id, "text": fact.text, "recovery_plan_id": str(recovery_id),
            "plan_id": str(comparison.candidate_plan_id), "comparison_at": data["comparison_at"],
        } for fact in facts])

    def explain_alert(self, business_date, order_id=None, alert_id=None):
        # Task 7's canonical read service has not shipped. Never infer an Alert
        # from Order.risk_status, and never invoke evaluation from a query.
        return self._envelope({"order_id": str(order_id) if order_id else None,
            "alert_id": str(alert_id) if alert_id else None, "alerts": None},
            business_date=business_date, missing=["ALERT_QUERY_UNAVAILABLE"], facts=[{
                "id": "alert_unavailable", "order_id": str(order_id) if order_id else None,
                "alert_id": str(alert_id) if alert_id else None,
                "text": "提醒数据不可用：U06 正式只读查询服务尚未接入，不能确认该对象存在活动或历史提醒。",
            }])

    def _envelope(self, data, *, business_date=None, missing=(), facts=()):
        return {**data, "business_date": str(business_date) if business_date else data.get("business_date"),
                "as_of": self.clock().isoformat(), "truncated": False,
                "missing_reasons": list(missing), "facts": list(facts)}
