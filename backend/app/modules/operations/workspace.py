"""One business-date snapshot for the Operations planning and execution page."""

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models.fleet import ResourceStatus
from app.db.models.planning import DeliveryPlanStatus, PlanOrderAssignmentStatus, RouteStatus, StopType
from app.db.models.recovery import IncidentStatus
from app.db.repositories.alert_repository import AlertRepository
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.operations.queries import OperationsQueryService


def _point(location) -> list[float]:
    return [float(location.longitude), float(location.latitude)]


def _route_path(route, stops) -> tuple[list[list[float]], str]:
    geometry = route.route_geometry
    if isinstance(geometry, dict) and geometry.get("type") == "LineString":
        coordinates = geometry.get("coordinates")
        if isinstance(coordinates, list) and len(coordinates) >= 2:
            return coordinates, "STORED_GEOMETRY"
    points = [_point(route.start_location), *(_point(stop.location) for stop in stops), _point(route.end_location)]
    return points, "STOP_CONNECTORS"


def _peak_load(stops) -> int:
    load = peak = 0
    for stop in stops:
        if stop.stop_type in (StopType.PICKUP, StopType.HANDOVER):
            load += stop.demand_load_units_snapshot
        elif stop.stop_type is StopType.DELIVERY:
            load -= stop.demand_load_units_snapshot
        peak = max(peak, load)
    return peak


class OperationsWorkspaceService:
    def __init__(self, session: Session) -> None:
        self.resources = ResourceRepository(session)
        self.fleet = FleetRepository(session)
        self.plans = PlanRepository(session)
        self.alerts = AlertRepository(session)
        self.operations = OperationsQueryService(session)

    def snapshot(self, business_date: date) -> dict:
        orders = self.resources.get_orders_for_business_date(business_date)
        operational_from = min((order.pickup_ready_at for order in orders), default=datetime.combine(business_date, time.min, timezone.utc))
        operational_until = max((order.delivery_window_end_at + timedelta(seconds=order.delivery_service_seconds) for order in orders), default=operational_from)
        assignments = self.fleet.get_vehicle_driver_pairs_for_window(operational_from, operational_until)
        usable = [item for item in assignments if item.vehicle.status is ResourceStatus.AVAILABLE and item.driver.status is ResourceStatus.AVAILABLE]
        merchants = {order.merchant_id: order.merchant for order in orders}.values()
        readiness = {
            "orders": len(orders),
            "merchants_ready": sum(item.preparation_status.value == "READY" for item in merchants),
            "merchants_preparing": sum(item.preparation_status.value == "PREPARING" for item in merchants),
            "merchants_delayed": sum(item.preparation_status.value == "DELAYED" for item in merchants),
            "vehicles": len({item.vehicle_id for item in usable}),
            "drivers": len({item.driver_id for item in usable}),
        }
        current = self.plans.get_current_plan(business_date)
        latest = self.plans.get_latest_plan_for_business_date(business_date) if current is None else None
        selected = current or (latest if latest and latest.status is DeliveryPlanStatus.DRAFT else None)
        if selected is None:
            return self._response(business_date, readiness, None, [], [], [], None)

        plan = self.plans.get_plan_for_operations_workspace(selected.id)
        assert plan is not None
        risk_by_order = {item.order_id: item.order.risk_status.value for item in plan.plan_orders}
        on_time_rate = None
        if plan.status is DeliveryPlanStatus.CURRENT:
            on_time_rate = self.operations._on_time(plan)[0].rate
        active_alerts = [item for item in self.alerts.list_active_for_business_date(business_date) if item.delivery_plan_id == plan.id] if plan.status is DeliveryPlanStatus.CURRENT else []
        alerted_orders = {item.order_id for item in active_alerts}
        routes = []
        for route in sorted(plan.routes, key=lambda item: item.route_no):
            stops = sorted(route.stops, key=lambda item: item.sequence_no)
            path, geometry_source = _route_path(route, stops)
            rendered_stops = []
            for stop in stops:
                risk = risk_by_order.get(stop.order_id, stop.order.risk_status.value)
                if stop.order_id in alerted_orders:
                    risk = "AT_RISK"
                rendered_stops.append({
                    "id": str(stop.id), "kind": stop.stop_type.value,
                    "order": stop.order.order_code, "place": stop.location.display_name,
                    "at": stop.planned_arrival_at.isoformat(), "point": _point(stop.location),
                    "execution": stop.status.value, "risk": risk,
                })
            at_risk = any(stop["kind"] == "DELIVERY" and stop["risk"] == "AT_RISK" for stop in rendered_stops)
            status = (
                "UNAVAILABLE" if route.vehicle.status is ResourceStatus.UNAVAILABLE else
                "CANCELLED" if route.status is RouteStatus.CANCELLED else
                "COMPLETED" if route.status is RouteStatus.COMPLETED else
                "AT_RISK" if at_risk else
                "PLANNED" if route.status is RouteStatus.PLANNED else "ON_ROUTE"
            )
            routes.append({
                "id": str(route.id), "route_no": route.route_no,
                "vehicle": route.vehicle.vehicle_code, "driver": route.driver.driver_code,
                "vehicle_id": str(route.vehicle_id), "distance_km": route.distance_meters / 1000,
                "vehicle_status": route.vehicle.status.value,
                "route_execution_status": route.status.value,
                "duration_min": round(route.duration_seconds / 60),
                "utilization": min(100, round(100 * _peak_load(stops) / route.vehicle_capacity_load_units_snapshot)),
                "status": status, "stops": rendered_stops, "path": path,
                "geometry_source": geometry_source,
                "road_aligned": (
                    geometry_source == "STORED_GEOMETRY"
                    and isinstance(route.route_metrics, dict)
                    and route.route_metrics.get("geometry_provider") == "OSRM"
                ),
            })
        incidents = [
            {"id": str(item.id), "title": item.incident_type.value.replace("_", " ").title(),
             "detail": f"{item.incident_code} · {item.status.value}", "level": "incident",
             "incident_id": str(item.id),
             "vehicle": next((route.vehicle.vehicle_code for route in plan.routes if route.vehicle_id == item.vehicle_id), "")}
            for item in plan.incidents if item.status is not IncidentStatus.RESOLVED
        ]
        assigned_route_by_order = {item.order_id: item.vehicle_route_id for item in plan.plan_orders}
        vehicle_by_route = {route["id"]: route["vehicle"] for route in routes}
        alerts = [
            {"id": str(item.id), "title": "Delivery window at risk",
             "detail": next((order.order_code for order in orders if order.id == item.order_id), str(item.order_id)),
             "level": "risk", "incident_id": None,
             "vehicle": vehicle_by_route.get(str(assigned_route_by_order.get(item.order_id)), "")}
            for item in active_alerts
        ]
        unassigned = [
            {"order": item.order.order_code, "reason": item.unassigned_reason_detail or item.unassigned_reason_code}
            for item in plan.plan_orders if item.assignment_status is PlanOrderAssignmentStatus.UNASSIGNED
        ]
        return self._response(business_date, readiness, plan, routes, alerts + incidents, unassigned, on_time_rate)

    @staticmethod
    def _response(business_date, readiness, plan, routes, alerts, unassigned, on_time_rate):
        return {
            "business_date": business_date.isoformat(),
            "readiness": readiness,
            "plan": ({"id": str(plan.id), "code": plan.plan_code, "version": plan.version_no,
                      "status": plan.status.value} if plan else None),
            "routes": routes, "alerts": alerts, "unassigned": unassigned,
            "on_time_rate": on_time_rate,
            "constraint_summary": ["Pickup before delivery", "Vehicle load capacity", "Merchant ready times", "Delivery time windows"],
        }
