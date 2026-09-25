"""Normal planning: materialize -> solve/validate outside transaction -> save DRAFT.

The bundled matrix is an explicit geographic estimate, not live road/traffic data.
Plans produced here never become CURRENT automatically.
"""
import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.db.models import DeliveryPlan, DeliveryPlanOrder, Driver, Location, Order, RouteStop, Vehicle, VehicleDriverAssignment, VehicleRoute
from app.db.repositories.fleet_repository import FleetRepository
from app.integrations.agent.contracts import RecoveryError
from app.integrations.optimization.solver import estimated_matrix, solve, validate


def timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc)


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PlanningService:
    def __init__(self, session_factory, *, business_timezone="Asia/Singapore", clock=lambda: datetime.now(timezone.utc),
                 matrix_provider=estimated_matrix, matrix_source="GEOGRAPHIC_ESTIMATE"):
        self.sessions, self.clock = session_factory, clock
        self.timezone = ZoneInfo(business_timezone)
        self.matrix_provider, self.matrix_source = matrix_provider, matrix_source

    def collect(self, session, day, start):
        orders = list(session.scalars(select(Order).where(Order.business_date == day).order_by(Order.id).limit(101)))
        if len(orders) > 100:
            raise RecoveryError("PLANNING_INPUT_LIMIT", "单次规划最多 100 个订单", 422)
        if not orders:
            raise RecoveryError("NO_ORDERS", "该日期没有订单", 409)
        if any(o.execution_status != "PLANNED" for o in orders):
            raise RecoveryError("ORDERS_ALREADY_STARTED", "订单已经开始执行，请使用异常恢复", 409)
        pairs = FleetRepository(session).get_active_vehicle_driver_pairs(utc(start))
        pairs = [p for p in pairs if p.vehicle.status == "AVAILABLE" and p.driver.status == "AVAILABLE"]
        used_locations = {o.pickup_location_id for o in orders} | {o.delivery_location_id for o in orders} | {p.vehicle.current_location_id for p in pairs}
        if len(pairs) > 100:
            raise RecoveryError("PLANNING_INPUT_LIMIT", "单次规划最多 100 组车人资源", 422)
        locations = list(session.scalars(select(Location).where(Location.id.in_(used_locations)).order_by(Location.id)))
        indexes = {l.id: i for i, l in enumerate(locations)}
        vehicles = [{"id": str(p.vehicle_id), "driver_id": str(p.driver_id), "assignment_id": str(p.id),
            "location": indexes[p.vehicle.current_location_id], "capacity": p.vehicle.capacity_load_units,
            "start": start, "end": timestamp(p.assigned_until_at) if p.assigned_until_at else start + 86400,
            "initial_load": 0} for p in pairs]
        order_data = [{"id": str(o.id), "pickup": indexes[o.pickup_location_id], "delivery": indexes[o.delivery_location_id],
            "pickup_kind": "PICKUP", "pickup_service": o.pickup_service_seconds, "delivery_service": o.delivery_service_seconds,
            "ready": timestamp(o.pickup_ready_at), "window_start": timestamp(o.delivery_window_start_at),
            "deadline": timestamp(o.delivery_window_end_at), "demand": o.demand_load_units,
            "allowed_vehicle_ids": [v["id"] for v in vehicles]} for o in orders]
        return {"orders": order_data, "vehicles": vehicles,
            "locations": [{"id": str(l.id), "latitude": float(l.latitude), "longitude": float(l.longitude)} for l in locations]}

    def generate(self, day, subject):
        start = max(timestamp(self.clock()) + 300, timestamp(datetime.combine(day, time.min, self.timezone)))
        with self.sessions() as session:
            snapshot = self.collect(session, day, start)
        distance, duration = self.matrix_provider(snapshot["locations"])
        payload = json.dumps({**snapshot, "distance_matrix": distance, "duration_matrix": duration})
        result = solve(payload)
        if result.status != "FEASIBLE":
            raise RecoveryError("PLANNING_" + result.status,
                "无法生成满足约束的方案" if result.status == "INFEASIBLE" else "求解未完成或发生错误", 409 if result.status == "INFEASIBLE" else 503)
        report = validate(payload, result)
        if report.status != "VALID":
            raise RecoveryError("PLANNING_VALIDATION_FAILED", "路线未通过独立校验", 500)
        output = json.loads(result.solution_payload_json)
        with self.sessions() as session, session.begin():
            # Serialize this planning command per day; the existing current-plan
            # uniqueness guard remains authoritative for later activation.
            if session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": day.toordinal()})
            for model in (Order, Vehicle, Driver, VehicleDriverAssignment, Location):
                list(session.scalars(select(model).order_by(model.id).with_for_update()))
            if fingerprint(self.collect(session, day, start)) != fingerprint(snapshot):
                raise RecoveryError("PLANNING_SNAPSHOT_STALE", "求解期间资源或订单变化，请重新生成", 409)
            existing = session.scalar(select(DeliveryPlan.id).where(DeliveryPlan.business_date == day,
                DeliveryPlan.status.in_(("DRAFT", "CANDIDATE", "CURRENT"))))
            if existing:
                raise RecoveryError("PLAN_ALREADY_EXISTS", "已有待处理或执行中的计划，请先处理已有计划", 409)
            plan_id = uuid4()
            plan = DeliveryPlan(id=plan_id, plan_code="PLAN-" + plan_id.hex[:24], plan_group_id=uuid4(),
                business_date=day, version_no=1, status="DRAFT", solver_engine="OR_TOOLS", validation_status="VALID",
                assigned_order_count=len(snapshot["orders"]), unassigned_order_count=0,
                vehicle_count=len(output["routes"]), total_distance_meters=report.summary.total_distance_meters,
                total_duration_seconds=report.summary.total_duration_seconds, created_by=subject,
                validation_summary={**report.model_dump(mode="json"), "matrix_source": self.matrix_source,
                    "snapshot_token": fingerprint(snapshot), "planning_start": start})
            session.add(plan)
            session.flush()
            self.save_routes(session, plan_id, snapshot, output)
            session.flush()
        return {"plan_id": str(plan_id), "status": "DRAFT", "validation_status": "VALID",
            "matrix_source": self.matrix_source, "requires_human_review": True,
            "summary": report.summary.model_dump(mode="json"), "routes": output["routes"],
            "notice": "估算距离和时长，未使用实时路网；计划尚未生效。" if self.matrix_source == "GEOGRAPHIC_ESTIMATE" else "计划尚未生效。"}

    @staticmethod
    def save_routes(session, plan_id, snapshot, output):
        from uuid import UUID
        vehicles = {v["id"]: v for v in snapshot["vehicles"]}
        orders = {o["id"]: o for o in snapshot["orders"]}
        for number, route in enumerate(output["routes"], 1):
            v, route_id = vehicles[route["vehicle_id"]], uuid4()
            loc = lambda index: UUID(snapshot["locations"][index]["id"])
            session.add(VehicleRoute(id=route_id, delivery_plan_id=plan_id, route_no=number,
                vehicle_id=UUID(v["id"]), driver_id=UUID(v["driver_id"]), vehicle_driver_assignment_id=UUID(v["assignment_id"]),
                start_location_id=loc(v["location"]), end_location_id=loc(route["stops"][-1]["location"]),
                status="PLANNED", planned_start_at=utc(route["start"]), planned_end_at=utc(route["end"]),
                distance_meters=route["distance_meters"], duration_seconds=route["duration_seconds"],
                vehicle_capacity_load_units_snapshot=v["capacity"]))
            session.flush()
            pickups = {}
            for sequence, stop in enumerate(route["stops"], 1):
                stop_id, o = uuid4(), orders[stop["order_id"]]
                delivery = stop["kind"] == "DELIVERY"
                if not delivery:
                    pickups[o["id"]] = stop_id
                session.add(RouteStop(id=stop_id, vehicle_route_id=route_id, order_id=UUID(o["id"]),
                    location_id=loc(stop["location"]), stop_type=stop["kind"], sequence_no=sequence,
                    precedence_stop_id=pickups[o["id"]] if delivery else None,
                    planned_arrival_at=utc(stop["arrival"]), planned_departure_at=utc(stop["departure"]),
                    service_seconds=o["delivery_service"] if delivery else o["pickup_service"],
                    time_window_start_at=utc(o["window_start"] if delivery else o["ready"]),
                    time_window_end_at=utc(o["deadline"]), demand_load_units_snapshot=o["demand"], status="PLANNED"))
                session.flush()
                if delivery:
                    session.add(DeliveryPlanOrder(id=uuid4(), delivery_plan_id=plan_id, order_id=UUID(o["id"]),
                        assignment_status="ASSIGNED", vehicle_route_id=route_id))

    def activate(self, plan_id, subject):
        with self.sessions() as session, session.begin():
            plan = session.get(DeliveryPlan, plan_id)
            if plan is None:
                raise RecoveryError("PLAN_NOT_FOUND", "计划不存在", 404)
            if session.bind.dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": plan.business_date.toordinal()})
            for model in (Order, Vehicle, Driver, VehicleDriverAssignment, Location, DeliveryPlan):
                list(session.scalars(select(model).order_by(model.id).with_for_update().execution_options(populate_existing=True)))
            if plan.status != "DRAFT" or plan.version_no != 1 or plan.validation_status != "VALID":
                raise RecoveryError("PLAN_NOT_ACTIVATABLE", "只能确认已校验的初始草稿计划", 409)
            if session.scalar(select(DeliveryPlan.id).where(DeliveryPlan.business_date == plan.business_date, DeliveryPlan.status == "CURRENT")):
                raise RecoveryError("CURRENT_PLAN_EXISTS", "该日期已有当前计划", 409)
            summary = plan.validation_summary or {}
            if not summary.get("snapshot_token"):
                raise RecoveryError("PLAN_REVALIDATION_REQUIRED", "草稿缺少快照校验信息", 409)
            fresh = self.collect(session, plan.business_date, summary["planning_start"])
            if fingerprint(fresh) != summary["snapshot_token"]:
                raise RecoveryError("PLANNING_SNAPSHOT_STALE", "规划输入已经变化，请重新生成", 409)
            if summary["planning_start"] < timestamp(self.clock()):
                raise RecoveryError("PLAN_SCHEDULE_STALE", "计划开始时间已过，请取消草稿并重新生成", 409)
            plan.status, plan.activated_at = "CURRENT", self.clock()
            plan.validation_summary = {**summary, "activated_by": subject}
            return {"plan_id": str(plan.id), "status": "CURRENT"}

    def cancel(self, plan_id):
        with self.sessions() as session, session.begin():
            plan = session.scalar(select(DeliveryPlan).where(DeliveryPlan.id == plan_id).with_for_update())
            if plan is None:
                raise RecoveryError("PLAN_NOT_FOUND", "计划不存在", 404)
            if plan.status != "DRAFT":
                raise RecoveryError("PLAN_NOT_CANCELLABLE", "此接口只能取消初始草稿", 409)
            plan.status = "CANCELLED"
            return {"plan_id": str(plan.id), "status": "CANCELLED"}
