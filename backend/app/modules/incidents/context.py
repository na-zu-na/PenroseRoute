"""Resolve and lock facts for deterministic incident assessment."""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import DeliveryPlan, Merchant, Order, RouteStop, Vehicle, VehicleRoute
from app.db.models.planning import DeliveryPlanStatus, StopStatus, StopType
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository


@dataclass(frozen=True)
class VehicleIncidentContext:
    plan: DeliveryPlan
    route: VehicleRoute
    vehicle: Vehicle
    orders: tuple[Order, ...]


@dataclass(frozen=True)
class MerchantOrderContext:
    order: Order
    route_id: UUID
    is_direct: bool
    delivery_stop: RouteStop | None


@dataclass(frozen=True)
class MerchantIncidentContext:
    plan: DeliveryPlan
    merchant: Merchant
    original_ready_at: datetime
    orders: tuple[MerchantOrderContext, ...]
    remaining_stops: tuple[RouteStop, ...]


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


def resolve_merchant_incident_context(
    session: Session, *, business_date: date, merchant_id: UUID
) -> MerchantIncidentContext:
    plans = PlanRepository(session)
    resources = ResourceRepository(session)

    plan = plans.get_current_plan(business_date)
    if plan is None:
        raise NotFound(
            code="CURRENT_PLAN_NOT_FOUND",
            message=f"No current delivery plan for {business_date.isoformat()}",
        )
    if resources.get_merchant_by_id(merchant_id) is None:
        raise NotFound(code="MERCHANT_NOT_FOUND", message="Merchant was not found")
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
    merchant = resources.lock_merchant_by_id(merchant_id)
    if merchant is None:
        raise NotFound(code="MERCHANT_NOT_FOUND", message="Merchant was not found")
    pickup_stops = plans.lock_merchant_pickup_stops(plan.id, merchant_id)
    if not pickup_stops:
        raise Conflict(
            code="MERCHANT_NOT_IN_CURRENT_PLAN",
            message="Merchant has no pickup stop in the current plan",
        )
    if any(stop.time_window_start_at is None for stop in pickup_stops):
        raise Conflict(
            code="INCIDENT_FACT_CONFLICT",
            message="Merchant pickup stop is missing its ready-time snapshot",
        )

    route_ids = sorted({stop.vehicle_route_id for stop in pickup_stops})
    route_stops = plans.lock_route_stops_for_routes(route_ids)
    stops_by_route: dict[UUID, list[RouteStop]] = {route_id: [] for route_id in route_ids}
    for stop in route_stops:
        stops_by_route[stop.vehicle_route_id].append(stop)
    direct_by_route: dict[UUID, set[UUID]] = {route_id: set() for route_id in route_ids}
    cutoff_by_route: dict[UUID, int] = {}
    for stop in pickup_stops:
        direct_by_route[stop.vehicle_route_id].add(stop.order_id)
        cutoff_by_route[stop.vehicle_route_id] = min(
            cutoff_by_route.get(stop.vehicle_route_id, stop.sequence_no),
            stop.sequence_no,
        )

    memberships = [
        item
        for route_id in route_ids
        for item in plans.list_route_plan_orders(route_id)
    ]
    orders = resources.lock_orders_by_ids([item.order_id for item in memberships])
    orders_by_id = {order.id: order for order in orders}
    if len(orders_by_id) != len(memberships):
        raise Conflict(
            code="INCIDENT_FACT_CONFLICT",
            message="Merchant route order facts changed during assessment",
        )

    result: list[MerchantOrderContext] = []
    for membership in memberships:
        route_id = membership.vehicle_route_id
        if route_id is None:
            continue
        stops = [
            stop
            for stop in stops_by_route[route_id]
            if stop.order_id == membership.order_id
        ]
        is_direct = membership.order_id in direct_by_route[route_id]
        is_downstream = any(
            stop.sequence_no > cutoff_by_route[route_id] for stop in stops
        )
        if not is_direct and not is_downstream:
            continue
        result.append(
            MerchantOrderContext(
                order=orders_by_id[membership.order_id],
                route_id=route_id,
                is_direct=is_direct,
                delivery_stop=next(
                    (stop for stop in stops if stop.stop_type is StopType.DELIVERY),
                    None,
                ),
            )
        )
    result.sort(key=lambda item: item.order.order_code)
    return MerchantIncidentContext(
        plan=locked_plan,
        merchant=merchant,
        original_ready_at=min(
            stop.time_window_start_at for stop in pickup_stops
            if stop.time_window_start_at is not None
        ),
        orders=tuple(result),
        remaining_stops=tuple(
            stop
            for route_id in route_ids
            for stop in stops_by_route[route_id]
            if stop.sequence_no >= cutoff_by_route[route_id]
            and stop.status is not StopStatus.COMPLETED
        ),
    )
