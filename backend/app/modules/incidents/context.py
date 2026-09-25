"""Resolve and lock facts for deterministic incident assessment."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import DeliveryPlan, Order, Vehicle, VehicleRoute
from app.db.models.planning import DeliveryPlanStatus
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository


@dataclass(frozen=True)
class VehicleIncidentContext:
    plan: DeliveryPlan
    route: VehicleRoute
    vehicle: Vehicle
    orders: tuple[Order, ...]


def resolve_vehicle_incident_context(
    session: Session, *, business_date: date, vehicle_id: UUID
) -> VehicleIncidentContext:
    plans = PlanRepository(session)
    fleet = FleetRepository(session)
    resources = ResourceRepository(session)

    plan = plans.get_current_plan(business_date)
    if plan is None:
        raise NotFound(
            code="CURRENT_PLAN_NOT_FOUND",
            message=f"No current delivery plan for {business_date.isoformat()}",
        )
    if fleet.get_vehicle_by_id(vehicle_id) is None:
        raise NotFound(
            code="VEHICLE_NOT_FOUND",
            message="Vehicle was not found",
        )
    route = plans.lock_active_route_for_plan_vehicle(plan.id, vehicle_id)
    if route is None:
        raise Conflict(
            code="VEHICLE_NOT_EXECUTING_ROUTE",
            message="Vehicle is not executing an active route in the current plan",
        )
    locked_plan = plans.lock_plan_by_id(plan.id)
    if (
        locked_plan is None
        or locked_plan.business_date != business_date
        or locked_plan.status is not DeliveryPlanStatus.CURRENT
    ):
        raise Conflict(
            code="BASE_PLAN_NOT_CURRENT",
            message="The resolved delivery plan is no longer current",
        )
    vehicle = fleet.lock_vehicle_by_id(vehicle_id)
    if vehicle is None:
        raise NotFound(code="VEHICLE_NOT_FOUND", message="Vehicle was not found")
    memberships = plans.list_route_plan_orders(route.id)
    locked_orders = resources.lock_orders_by_ids(
        [membership.order_id for membership in memberships]
    )
    orders_by_id = {order.id: order for order in locked_orders}
    if len(orders_by_id) != len(memberships):
        raise Conflict(
            code="INCIDENT_FACT_CONFLICT",
            message="Route order facts changed during incident assessment",
        )
    return VehicleIncidentContext(
        plan=locked_plan,
        route=route,
        vehicle=vehicle,
        orders=tuple(orders_by_id[item.order_id] for item in memberships),
    )
