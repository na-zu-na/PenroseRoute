"""SQLAlchemy recovery adapter. No session survives prepare/finish boundaries."""
import json
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from app.db.models import (
    DeliveryPlan, DeliveryPlanOrder, Driver, Incident, IncidentAffectedOrder, Location,
    Order, RecoveryPlan, RouteStop, Vehicle, VehicleDriverAssignment, VehicleRoute,
)
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.integrations.agent.contracts import AttemptRecord, ImpactFact, RecoveryContext, RecoveryError
from app.integrations.optimization.solver import estimated_matrix
from app.modules.planning.service import fingerprint, timestamp, utc
from .orchestration import PreparedAttempt


def row_data(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, (datetime, date)) else value
    return result


def row_values(model, data):
    result = dict(data)
    for column in model.__table__.columns:
        value = result.get(column.name)
        if value is None:
            continue
        kind = column.type.python_type
        if kind is UUID:
            result[column.name] = UUID(value) if isinstance(value, str) else value
        elif kind is datetime:
            result[column.name] = datetime.fromisoformat(value) if isinstance(value, str) else value
    return result


class SqlRecoveryApplication:
    def __init__(self, session_factory, *, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.clock = session_factory, clock

    @staticmethod
    def lock(session, incident_id):
        incident = session.get(Incident, incident_id)
        if incident is None:
            raise RecoveryError("INCIDENT_NOT_FOUND", "异常不存在", 404)
        base = session.get(DeliveryPlan, incident.delivery_plan_id)
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": base.business_date.toordinal()})
        # Shared deterministic lock order with normal planning; coarse-grained P0 locking.
        for model in (Order, Vehicle, Driver, VehicleDriverAssignment, Location, DeliveryPlan, VehicleRoute, RouteStop, Incident, RecoveryPlan):
            list(session.scalars(select(model).order_by(model.id).with_for_update().execution_options(populate_existing=True)))
        if base.status != "CURRENT":
            raise RecoveryError("BASE_PLAN_NOT_CURRENT", "原计划已不是当前执行版本", 409)
        if incident.status == "RESOLVED":
            raise RecoveryError("INCIDENT_RESOLVED", "异常已经解决", 409)
        return incident, base

    def snapshot(self, session, incident, base, scope, current_time):
        plan = PlanRepository(session).get_plan_with_routes(base.id)
        if len(plan.plan_orders) > 100:
            raise RecoveryError("RECOVERY_INPUT_LIMIT", "当前实现最多处理 100 个计划订单", 422)
        all_orders = {o.id: o for o in session.scalars(select(Order).where(Order.id.in_([m.order_id for m in plan.plan_orders])))}
        impacts = list(session.scalars(select(IncidentAffectedOrder).where(IncidentAffectedOrder.incident_id == incident.id).order_by(IncidentAffectedOrder.order_id)))
        impacted_ids = {i.original_vehicle_route_id for i in impacts if i.requires_replanning}
        if incident.vehicle_route_id:
            impacted_ids.add(incident.vehicle_route_id)
        if incident.incident_type == "MERCHANT_DELAY" and (incident.delay_seconds is None or incident.delay_seconds <= 600):
            raise RecoveryError("RECOVERY_NOT_REQUIRED", "延迟不超过 600 秒，无需恢复重规划", 409)
        if not impacts or not any(i.requires_replanning for i in impacts):
            raise RecoveryError("RECOVERY_NOT_REQUIRED", "没有需要重规划的订单影响记录", 409)
        for impact in impacts:
            if all_orders[impact.order_id].execution_status != impact.execution_status_snapshot:
                raise RecoveryError("INCIDENT_IMPACT_STALE", "订单执行状态变化，需重新评估异常影响", 409)
        selected_routes = {r.id for r in plan.routes if scope == "ALL_REMAINING" or r.id in impacted_ids}
        selected = [m for m in plan.plan_orders if m.vehicle_route_id in selected_routes
                    and all_orders[m.order_id].execution_status != "COMPLETED"]
        route_by_id = {r.id: r for r in plan.routes}
        pairs = FleetRepository(session).get_active_vehicle_driver_pairs(current_time)
        if len(selected) > 100 or len(pairs) > 100:
            raise RecoveryError("RECOVERY_INPUT_LIMIT", "单次恢复最多 100 个订单、100 组车人资源", 422)
        used_locations = {o.pickup_location_id for o in all_orders.values()} | {o.delivery_location_id for o in all_orders.values()}
        used_locations |= {p.vehicle.current_location_id for p in pairs} | {r.start_location_id for r in plan.routes}
        used_locations |= {s.location_id for r in plan.routes for s in r.stops}
        if incident.incident_location_id:
            used_locations.add(incident.incident_location_id)
        location_rows = list(session.scalars(select(Location).where(Location.id.in_(used_locations)).order_by(Location.id)))
        locations = [{"id": str(l.id), "latitude": float(l.latitude), "longitude": float(l.longitude)} for l in location_rows]
        location_index = {UUID(l["id"]): i for i, l in enumerate(locations)}
        preserved = []
        for route in plan.routes:
            stops = route.stops if route.id not in selected_routes else [s for s in route.stops if s.status == "COMPLETED"]
            if route.id in selected_routes:
                if any(s.status in ("ARRIVED", "IN_SERVICE") for s in route.stops):
                    raise RecoveryError("STOP_IN_SERVICE", "存在正在服务的停靠点，请先确认执行状态", 409)
                if [s.sequence_no for s in stops] != list(range(1, len(stops)+1)):
                    raise RecoveryError("COMPLETED_PREFIX_INVALID", "已完成停靠点不是连续前缀，需要人工核对", 409)
            if route.id not in selected_routes or stops:
                values = row_data(route)
                if route.id in selected_routes:
                    last = stops[-1]
                    end = last.actual_departure_at or last.planned_departure_at
                    values.update(planned_end_at=end.isoformat(), status="COMPLETED", actual_end_at=None,
                                  duration_seconds=max(0, timestamp(end)-timestamp(route.planned_start_at)))
                    # Prefix distance uses the same explicitly estimated geometry.
                    chain = [route.start_location_id] + [s.location_id for s in stops]
                    small = [locations[location_index[i]] for i in chain]
                    matrix, _ = estimated_matrix(small)
                    values["distance_meters"] = sum(matrix[i][i+1] for i in range(len(chain)-1))
                    values["end_location_id"] = str(last.location_id)
                preserved.append({"route": values, "stops": [row_data(s) for s in stops]})
        blocked_vehicles = {r.vehicle_id for r in plan.routes if r.id not in selected_routes}
        local_vehicles = {r.vehicle_id for r in plan.routes if r.id in selected_routes}
        prefix_assignments = {r["route"]["vehicle_id"]: r["route"]["vehicle_driver_assignment_id"] for r in preserved if r["stops"]}
        eligible = [p for p in pairs if p.vehicle.status != "UNAVAILABLE" and p.driver.status != "UNAVAILABLE"
            and p.vehicle_id != incident.vehicle_id and p.vehicle_id not in blocked_vehicles
            and (scope != "AFFECTED_ROUTE" or p.vehicle_id in local_vehicles)
            and (str(p.vehicle_id) not in prefix_assignments or prefix_assignments[str(p.vehicle_id)] == str(p.id))
            and (p.assigned_until_at is None or timestamp(p.assigned_until_at) > timestamp(current_time)+300)]
        now = timestamp(current_time)
        vehicles = [{"id": str(p.vehicle_id), "driver_id": str(p.driver_id), "assignment_id": str(p.id),
            "location": location_index[p.vehicle.current_location_id], "capacity": p.vehicle.capacity_load_units,
            "start": now+300, "end": timestamp(p.assigned_until_at) if p.assigned_until_at else now+86400,
            "initial_load": 0} for p in eligible]
        by_vehicle = {v["id"]: v for v in vehicles}
        order_data = []
        for member in selected:
            order, old_route = all_orders[member.order_id], route_by_id[member.vehicle_route_id]
            picked = order.execution_status in ("PICKED_UP", "DELIVERING")
            handover = picked and old_route.vehicle_id == incident.vehicle_id
            predecessor = next((s for s in old_route.stops if s.order_id == order.id and s.stop_type in ("PICKUP", "HANDOVER") and s.status == "COMPLETED"), None)
            pickup = location_index[incident.incident_location_id] if handover else location_index[order.pickup_location_id]
            allowed = [v["id"] for v in vehicles]
            if picked and not handover:
                allowed = [str(old_route.vehicle_id)] if str(old_route.vehicle_id) in by_vehicle else []
                if not predecessor:
                    raise RecoveryError("PICKUP_FACT_MISSING", "已取货订单缺少已完成取货事实", 409)
                if allowed:
                    by_vehicle[allowed[0]]["initial_load"] += order.demand_load_units
                pickup = None
            ready = timestamp(order.pickup_ready_at)
            if incident.incident_type == "MERCHANT_DELAY" and order.merchant_id == incident.merchant_id:
                ready = max(ready, timestamp(incident.updated_ready_at))
            order_data.append({"id": str(order.id), "pickup": pickup, "delivery": location_index[order.delivery_location_id],
                "pickup_kind": "HANDOVER" if handover else "PICKUP", "pickup_service": order.pickup_service_seconds,
                "delivery_service": order.delivery_service_seconds, "ready": max(now, ready),
                "window_start": timestamp(order.delivery_window_start_at), "deadline": timestamp(order.delivery_window_end_at),
                "demand": order.demand_load_units, "allowed_vehicle_ids": allowed,
                "precedence_stop_id": str(predecessor.id) if predecessor else None,
                "source_incident_id": str(incident.id) if handover else None})
        selected_ids = {m.order_id for m in selected}
        return {"orders": order_data, "vehicles": vehicles, "locations": locations,
            "base_plan": row_data(base),
            "base_routes": [{"route": row_data(r), "stops": [row_data(s) for s in r.stops]} for r in plan.routes],
            "base_memberships": [row_data(m) for m in plan.plan_orders],
            "preserved_routes": preserved,
            "preserved_memberships": [row_data(m) for m in plan.plan_orders if m.order_id not in selected_ids],
            "source_order_facts": [row_data(all_orders[key]) for key in sorted(all_orders)],
            "source_incident": {k: v for k, v in row_data(incident).items() if k not in ("status", "updated_at")},
            "impacts": [row_data(i) for i in impacts]}

    def prepare_attempt(self, incident_id, previous, next_scope):
        now = self.clock()
        with self.sessions() as session, session.begin():
            incident, base = self.lock(session, incident_id)
            latest = session.scalar(select(RecoveryPlan).where(RecoveryPlan.incident_id == incident_id).order_by(RecoveryPlan.attempt_no.desc()))
            if latest and (latest.status == "PENDING_REVIEW" or latest.solver_status is None):
                raise RecoveryError("RECOVERY_IN_PROGRESS", "已有执行中或待审核的恢复尝试", 409)
            if previous and (not latest or latest.id != previous.recovery_plan_id or latest.solver_status != "INFEASIBLE"):
                raise RecoveryError("RECOVERY_ATTEMPT_CONFLICT", "恢复链已发生变化", 409)
            if previous:
                from app.modules.incidents.scope import next_scope as following_scope
                if next_scope != following_scope(latest.replanning_scope):
                    raise RecoveryError("RECOVERY_SCOPE_INVALID", "范围必须按固定顺序升级", 422)
            context, snapshot = self.create_attempt(session, incident, base, latest, next_scope or "AFFECTED_ROUTE", now)
        # Matrix computation occurs after commit, just like solver/model calls.
        snapshot["distance_matrix"], snapshot["duration_matrix"] = estimated_matrix(snapshot["locations"])
        return PreparedAttempt(context, json.dumps(snapshot))

    def create_attempt(self, session, incident, base, latest, scope, now):
        snapshot = self.snapshot(session, incident, base, scope, now)
        recovery_id = uuid4()
        context = RecoveryContext(recovery_plan_id=recovery_id, incident_id=incident.id, business_date=base.business_date,
            base_delivery_plan_id=base.id, attempt_no=latest.attempt_no+1 if latest else 1,
            previous_recovery_plan_id=latest.id if latest else None, incident_type=incident.incident_type,
            requires_replanning=True, replanning_scope=scope, scope_description=f"确定性恢复范围 {scope}",
            current_time=now, snapshot_token=fingerprint(snapshot), travel_time_source="GEOGRAPHIC_ESTIMATE", incident_location_id=incident.incident_location_id,
            delay_seconds=incident.delay_seconds, frozen_stop_ids=tuple(UUID(s["id"]) for r in snapshot["preserved_routes"]
                for s in r["stops"] if s["status"] == "COMPLETED"),
            affected_orders=tuple(ImpactFact(**{k: i[k] for k in ImpactFact.model_fields}) for i in snapshot["impacts"]),
            incident_summary=f"{incident.incident_type}；车辆={incident.vehicle_id}；商家={incident.merchant_id}；延迟秒数={incident.delay_seconds}",
            incident_facts={k: snapshot["source_incident"][k] for k in ("incident_type", "vehicle_id", "merchant_id", "incident_location_id", "delay_seconds", "original_ready_at", "updated_ready_at", "detected_at")},
            current_plan_summary={k: snapshot["base_plan"][k] for k in ("id", "version_no", "status", "assigned_order_count", "unassigned_order_count", "vehicle_count", "total_distance_meters", "total_duration_seconds")},
            available_resources=tuple({**v, "location_id": snapshot["locations"][v["location"]]["id"]} for v in snapshot["vehicles"]),
            previous_attempts=tuple({"recovery_plan_id": str(r.id), "attempt_no": r.attempt_no,
                "replanning_scope": r.replanning_scope, "solver_status": r.solver_status, "validation_status": r.validation_status}
                for r in session.scalars(select(RecoveryPlan).where(RecoveryPlan.incident_id == incident.id).order_by(RecoveryPlan.attempt_no))))
        from .context import validate_context
        validate_context(context)
        session.add(RecoveryPlan(id=recovery_id, recovery_code="REC-"+recovery_id.hex[:24], incident_id=incident.id,
            attempt_no=context.attempt_no, previous_recovery_plan_id=context.previous_recovery_plan_id,
            base_delivery_plan_id=base.id, status="DRAFT", replanning_scope=scope,
            scope_description=context.scope_description))
        incident.status = "REPLANNING"
        return context, snapshot

    def finish_attempt(self, prepared, result):
        try:
            return self._finish_attempt(prepared, result)
        except Exception as exc:
            # A stale/failed finalization must not leave an unfinishable running DRAFT.
            with self.sessions() as session, session.begin():
                attempt = session.scalar(select(RecoveryPlan).where(RecoveryPlan.id == prepared.context.recovery_plan_id).with_for_update())
                if attempt is not None and attempt.solver_status is None:
                    attempt.solver_status = "ERROR"
                    attempt.agent_explanation = "恢复最终保存失败，未创建候选。"
                    attempt.solver_validation_summary = {"diagnostic": getattr(exc, "code", "FINALIZATION_FAILED")}
            if isinstance(exc, RecoveryError):
                raise
            raise RecoveryError("RECOVERY_FINALIZATION_FAILED", "候选保存失败，未切换当前计划", 500) from exc

    def _finish_attempt(self, prepared, result):
        context, observed = prepared.context, result.observation
        with self.sessions() as session, session.begin():
            incident, base = self.lock(session, context.incident_id)
            attempt = session.get(RecoveryPlan, context.recovery_plan_id)
            if not attempt or attempt.solver_status is not None:
                raise RecoveryError("RECOVERY_ALREADY_FINALIZED", "恢复尝试不存在或已保存", 409)
            fresh = self.snapshot(session, incident, base, context.replanning_scope, context.current_time)
            if fingerprint(fresh) != context.snapshot_token:
                raise RecoveryError("RECOVERY_SNAPSHOT_STALE", "配送状态变化，候选未保存，请重新评估", 409)
            attempt.solver_status = observed.solver.status
            attempt.validation_status = observed.validation.status if observed.validation else None
            attempt.agent_explanation = result.agent_explanation
            attempt.solver_validation_summary = {**observed.model_dump(mode="json"),
                "explanation": result.explanation.model_dump(mode="json") if result.explanation else None,
                "explanation_source": result.explanation_source,
                "diagnostic_codes": list(result.diagnostic_codes), "tool_trace": list(result.tool_trace)}
            if result.reviewable:
                snapshot, output = json.loads(prepared.optimization_input_json), json.loads(observed.solver.solution_payload_json)
                candidate_id = uuid4()
                version = session.scalar(select(func.max(DeliveryPlan.version_no)).where(DeliveryPlan.plan_group_id == base.plan_group_id)) + 1
                candidate = DeliveryPlan(id=candidate_id, plan_code="PLAN-"+candidate_id.hex[:24], plan_group_id=base.plan_group_id,
                    business_date=base.business_date, version_no=version, parent_plan_id=base.id, status="CANDIDATE",
                    solver_engine="OR_TOOLS", validation_status="VALID", assigned_order_count=len(snapshot["orders"])+sum(m["assignment_status"] == "ASSIGNED" for m in snapshot["preserved_memberships"]),
                    unassigned_order_count=sum(m["assignment_status"] == "UNASSIGNED" for m in snapshot["preserved_memberships"]), vehicle_count=0, created_by="recovery-workflow",
                    validation_summary={"matrix_source": "GEOGRAPHIC_ESTIMATE", "recovery_plan_id": str(attempt.id),
                        "snapshot_token": context.snapshot_token, "snapshot_time": context.current_time.isoformat(),
                        "scope": context.replanning_scope})
                session.add(candidate)
                session.flush()
                self.save_candidate(session, candidate, snapshot, output)
                attempt.candidate_delivery_plan_id = candidate_id
                attempt.status, incident.status = "PENDING_REVIEW", "REVIEW"
            session.flush()
            return AttemptRecord(recovery_plan_id=attempt.id, incident_id=attempt.incident_id, attempt_no=attempt.attempt_no,
                previous_recovery_plan_id=attempt.previous_recovery_plan_id, replanning_scope=attempt.replanning_scope,
                status=attempt.status, solver_status=attempt.solver_status, validation_status=attempt.validation_status,
                candidate_delivery_plan_id=attempt.candidate_delivery_plan_id, agent_explanation=attempt.agent_explanation)

    @staticmethod
    def save_candidate(session, candidate, snapshot, output):
        new_routes, route_mapping, stop_mapping = {}, {}, {}
        for item in snapshot["preserved_routes"]:
            route_mapping[item["route"]["id"]] = uuid4()
            stop_mapping.update({s["id"]: uuid4() for s in item["stops"]})
        for item in snapshot["preserved_routes"]:
            values = row_values(VehicleRoute, item["route"])
            values.update(id=route_mapping[item["route"]["id"]], delivery_plan_id=candidate.id, route_no=len(new_routes)+1)
            route = VehicleRoute(**values)
            session.add(route)
            session.flush()
            new_routes[str(route.vehicle_id)] = route
            for s in item["stops"]:
                values = row_values(RouteStop, s)
                values.update(id=stop_mapping[s["id"]], vehicle_route_id=route.id,
                    precedence_stop_id=stop_mapping.get(s["precedence_stop_id"]))
                session.add(RouteStop(**values))
        # Flush in sequence order so self references exist even on immediate FK engines.
        session.flush()
        for item in snapshot["preserved_memberships"]:
            values = row_values(DeliveryPlanOrder, item)
            values.update(id=uuid4(), delivery_plan_id=candidate.id,
                          vehicle_route_id=route_mapping.get(item["vehicle_route_id"]))
            session.add(DeliveryPlanOrder(**values))
        orders, vehicles = {o["id"]: o for o in snapshot["orders"]}, {v["id"]: v for v in snapshot["vehicles"]}
        loc = lambda index: UUID(snapshot["locations"][index]["id"])
        for calculated in output["routes"]:
            v = vehicles[calculated["vehicle_id"]]
            route = new_routes.get(v["id"])
            if route is None:
                route = VehicleRoute(id=uuid4(), delivery_plan_id=candidate.id, route_no=len(new_routes)+1,
                    vehicle_id=UUID(v["id"]), driver_id=UUID(v["driver_id"]), vehicle_driver_assignment_id=UUID(v["assignment_id"]),
                    start_location_id=loc(v["location"]), end_location_id=loc(v["location"]),
                    status="PLANNED", planned_start_at=utc(calculated["start"]), planned_end_at=utc(calculated["end"]),
                    distance_meters=0, duration_seconds=0, vehicle_capacity_load_units_snapshot=v["capacity"])
                session.add(route)
                new_routes[v["id"]] = route
            route.status = "ACTIVE" if route.actual_start_at else "PLANNED"
            route.planned_end_at = utc(calculated["end"])
            route.end_location_id = loc(calculated["stops"][-1]["location"])
            route.distance_meters += calculated["distance_meters"]
            route.duration_seconds = calculated["end"] - timestamp(route.planned_start_at)
            session.flush()
            sequence = session.scalar(select(func.max(RouteStop.sequence_no)).where(RouteStop.vehicle_route_id == route.id)) or 0
            pickups = {}
            for stop in calculated["stops"]:
                sequence += 1
                o, stop_id = orders[stop["order_id"]], uuid4()
                delivery = stop["kind"] == "DELIVERY"
                if not delivery:
                    pickups[o["id"]] = stop_id
                predecessor = pickups.get(o["id"], stop_mapping.get(o.get("precedence_stop_id"))) if delivery else None
                session.add(RouteStop(id=stop_id, vehicle_route_id=route.id, order_id=UUID(o["id"]),
                    location_id=loc(stop["location"]), stop_type=stop["kind"], sequence_no=sequence,
                    precedence_stop_id=predecessor, source_incident_id=UUID(o["source_incident_id"]) if stop["kind"] == "HANDOVER" else None,
                    planned_arrival_at=utc(stop["arrival"]), planned_departure_at=utc(stop["departure"]),
                    service_seconds=o["delivery_service"] if delivery else o["pickup_service"],
                    time_window_start_at=utc(o["window_start"] if delivery else o["ready"]), time_window_end_at=utc(o["deadline"]),
                    demand_load_units_snapshot=o["demand"], status="PLANNED"))
                session.flush()
                if delivery:
                    session.add(DeliveryPlanOrder(id=uuid4(), delivery_plan_id=candidate.id, order_id=UUID(o["id"]),
                        assignment_status="ASSIGNED", vehicle_route_id=route.id))
        candidate.vehicle_count = len(new_routes)
        candidate.total_distance_meters = sum(r.distance_meters for r in new_routes.values())
        candidate.total_duration_seconds = sum(r.duration_seconds for r in new_routes.values())
