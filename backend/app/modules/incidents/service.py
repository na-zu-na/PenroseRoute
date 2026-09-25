"""Explicit incident reporting and deterministic impact calculation."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, text
from app.db.models import DeliveryPlan, Incident, IncidentAffectedOrder, Location, Merchant, Order, VehicleRoute
from app.db.repositories.plan_repository import PlanRepository
from app.integrations.agent.contracts import RecoveryError
from app.modules.planning.service import timestamp, utc


class IncidentService:
    def __init__(self, sessions, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.clock = sessions, clock

    def report(self, command, subject):
        with self.sessions() as session, session.begin():
            if session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": command.business_date.toordinal()})
            current = PlanRepository(session).get_current_plan(command.business_date)
            if current is None:
                raise RecoveryError("CURRENT_PLAN_NOT_FOUND", "该日期没有执行中的计划", 404)
            plan = PlanRepository(session).get_plan_with_routes(current.id)
            orders = {o.id: o for o in session.scalars(select(Order).where(Order.id.in_([m.order_id for m in plan.plan_orders])).with_for_update())}
            now, incident_id = self.clock(), uuid4()
            vehicle = command.incident_type == "VEHICLE_UNAVAILABLE"
            if vehicle:
                route = next((r for r in plan.routes if r.id == command.vehicle_route_id), None)
                if route is None or route.status in ("COMPLETED", "CANCELLED"):
                    raise RecoveryError("ROUTE_NOT_ACTIVE", "路线不属于当前可执行计划", 409)
                if session.get(Location, command.incident_location_id) is None:
                    raise RecoveryError("LOCATION_NOT_FOUND", "事故地点不存在", 404)
                if not any(m.vehicle_route_id == route.id and orders[m.order_id].execution_status != "COMPLETED" for m in plan.plan_orders):
                    raise RecoveryError("NO_REMAINING_TASKS", "该路线没有未完成订单", 409)
                match = Incident.vehicle_id == route.vehicle_id
                affected_routes, delay = {route.id}, None
                incident = Incident(id=incident_id, incident_code="INC-"+incident_id.hex[:24], incident_type=command.incident_type,
                    status="DETECTED", delivery_plan_id=plan.id, vehicle_route_id=route.id, vehicle_id=route.vehicle_id,
                    incident_location_id=command.incident_location_id, detected_at=now, detected_by=subject)
            else:
                merchant = session.get(Merchant, command.merchant_id)
                if merchant is None:
                    raise RecoveryError("MERCHANT_NOT_FOUND", "商家不存在", 404)
                pending = [o for o in orders.values() if o.merchant_id == command.merchant_id
                           and o.execution_status in ("PLANNED", "PICKUP_IN_PROGRESS")]
                if not pending:
                    raise RecoveryError("MERCHANT_NO_PENDING_PICKUP", "当前计划没有该商家的待取货订单", 409)
                original = min(pickup.pickup_ready_at for pickup in pending)
                delay = timestamp(command.updated_ready_at)-timestamp(original)
                if delay <= 0:
                    raise RecoveryError("MERCHANT_DELAY_NOT_POSITIVE", "新备货时间必须晚于原备货时间", 422)
                affected_routes = {m.vehicle_route_id for m in plan.plan_orders if m.order_id in {o.id for o in pending} and m.vehicle_route_id}
                match = Incident.merchant_id == command.merchant_id
                incident = Incident(id=incident_id, incident_code="INC-"+incident_id.hex[:24], incident_type=command.incident_type,
                    status="DETECTED" if delay > 600 else "RESOLVED", resolved_at=None if delay > 600 else now,
                    delivery_plan_id=plan.id, merchant_id=command.merchant_id, original_ready_at=original,
                    updated_ready_at=command.updated_ready_at, delay_seconds=delay, detected_at=now, detected_by=subject)
            existing = session.scalar(select(Incident).where(Incident.delivery_plan_id == plan.id,
                Incident.incident_type == command.incident_type, match, Incident.status != "RESOLVED"))
            if existing:
                raise RecoveryError("INCIDENT_ALREADY_OPEN", "已有该资源的未解决异常，请处理已有异常", 409)
            if vehicle:
                route.vehicle.status = "UNAVAILABLE"
                route.vehicle.current_location_id = command.incident_location_id
                route.vehicle.current_location_recorded_at = now
            else:
                merchant.operational_ready_at, merchant.preparation_status = command.updated_ready_at, "DELAYED"
                for order in pending:
                    if timestamp(order.pickup_ready_at) < timestamp(command.updated_ready_at):
                        order.pickup_ready_at = command.updated_ready_at
                # Conservative delay propagation on each affected remaining route.
                for route in plan.routes:
                    if route.id not in affected_routes:
                        continue
                    first = next((s.sequence_no for s in route.stops if s.order_id in {o.id for o in pending}
                                  and s.stop_type == "PICKUP" and s.status != "COMPLETED"), None)
                    if first is None:
                        continue
                    for stop in route.stops:
                        if stop.sequence_no >= first and stop.status != "COMPLETED":
                            stop.planned_arrival_at = utc(timestamp(stop.planned_arrival_at)+delay)
                            stop.planned_departure_at = utc(timestamp(stop.planned_departure_at)+delay)
                            if stop.stop_type == "DELIVERY" and timestamp(stop.planned_arrival_at) > timestamp(orders[stop.order_id].delivery_window_end_at):
                                orders[stop.order_id].risk_status = "AT_RISK"
                    route.planned_end_at = utc(timestamp(route.planned_end_at)+delay)
                    route.duration_seconds += delay
            session.add(incident)
            session.flush()
            count = 0
            for member in plan.plan_orders:
                if member.vehicle_route_id not in affected_routes:
                    continue
                order = orders[member.order_id]
                completed = order.execution_status == "COMPLETED"
                picked = order.execution_status in ("PICKED_UP", "DELIVERING", "COMPLETED")
                requires = not completed and (vehicle or delay > 600)
                handover = vehicle and picked and not completed
                impact_type = "COMPLETED_FROZEN" if completed else "HANDOVER_REQUIRED" if handover else (
                    "PICKUP_REPLAN" if vehicle else "DOWNSTREAM_ROUTE_IMPACT" if requires else "WAITING_TIME_UPDATE")
                session.add(IncidentAffectedOrder(id=uuid4(), incident_id=incident_id, order_id=order.id,
                    original_vehicle_route_id=member.vehicle_route_id, execution_status_snapshot=order.execution_status,
                    risk_status_snapshot=order.risk_status, was_picked_up=picked, was_completed=completed,
                    requires_replanning=requires, handover_required=handover, impact_type=impact_type,
                    impact_reason="确定性车辆不可用规则" if vehicle else "商家延迟及下游等待传播规则", assessed_at=now))
                count += 1
            return {"incident_id": str(incident_id), "status": str(incident.status), "affected_order_count": count,
                    "delay_seconds": delay, "requires_replanning": vehicle or delay > 600}
