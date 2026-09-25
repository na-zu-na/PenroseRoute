"""Read models and query service for planning results."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.models import DeliveryPlan, DeliveryPlanOrder, RouteStop, VehicleRoute
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
)
from app.db.repositories.plan_repository import PlanRepository
from app.integrations.optimization.contracts import SolverResult
from app.modules.planning.input_builder import PlanningFacts


@dataclass(frozen=True, slots=True)
class PlanSummaryView:
    total_orders: int
    assigned_orders: int
    unassigned_orders: int
    vehicle_count: int
    total_distance_meters: int
    total_duration_seconds: int


@dataclass(frozen=True, slots=True)
class UnassignedOrderView:
    order_id: UUID
    order_code: str
    unassigned_reason_code: str
    unassigned_reason_detail: str | None


@dataclass(frozen=True, slots=True)
class GeneratedPlanView:
    delivery_plan_id: UUID
    plan_code: str
    business_date: date
    version_no: int
    status: str
    summary: PlanSummaryView
    unassigned_orders: tuple[UnassignedOrderView, ...]


@dataclass(frozen=True, slots=True)
class DeliveryPlanView:
    id: UUID
    plan_code: str
    plan_group_id: UUID
    business_date: date
    version_no: int
    parent_plan_id: UUID | None
    status: str
    solver_engine: str
    validation_status: str
    total_distance_meters: int | None
    total_duration_seconds: int | None
    vehicle_count: int
    assigned_order_count: int
    unassigned_order_count: int
    validation_summary: dict[str, Any] | None
    activated_at: datetime | None
    superseded_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PlanOrderMembershipView:
    order_id: UUID
    order_code: str
    assignment_status: str
    vehicle_route_id: UUID | None
    unassigned_reason_code: str | None
    unassigned_reason_detail: str | None


@dataclass(frozen=True, slots=True)
class VehicleRouteView:
    id: UUID
    delivery_plan_id: UUID
    route_no: int
    vehicle_id: UUID
    driver_id: UUID
    vehicle_driver_assignment_id: UUID
    start_location_id: UUID
    end_location_id: UUID
    status: str
    planned_start_at: datetime
    planned_end_at: datetime
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    distance_meters: int
    duration_seconds: int
    vehicle_capacity_load_units_snapshot: int
    route_geometry: dict[str, Any] | None
    route_metrics: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class RouteStopView:
    id: UUID
    vehicle_route_id: UUID
    order_id: UUID
    location_id: UUID
    stop_type: str
    sequence_no: int
    precedence_stop_id: UUID | None
    source_incident_id: UUID | None
    planned_arrival_at: datetime
    planned_departure_at: datetime
    actual_arrival_at: datetime | None
    actual_departure_at: datetime | None
    service_seconds: int
    time_window_start_at: datetime | None
    time_window_end_at: datetime | None
    demand_load_units_snapshot: int
    status: str


def build_generated_plan_view(
    plan: DeliveryPlan,
    facts: PlanningFacts,
    result: SolverResult,
) -> GeneratedPlanView:
    order_codes = {order.order_id: order.order_code for order in facts.orders}
    unassigned = tuple(
        UnassignedOrderView(
            order_id=item.order_id,
            order_code=order_codes[item.order_id],
            unassigned_reason_code=item.reason_code,
            unassigned_reason_detail=item.reason_detail,
        )
        for item in result.unassigned_orders
    )
    return GeneratedPlanView(
        delivery_plan_id=plan.id,
        plan_code=plan.plan_code,
        business_date=plan.business_date,
        version_no=plan.version_no,
        status=plan.status.value,
        summary=PlanSummaryView(
            total_orders=len(facts.orders),
            assigned_orders=len(facts.orders) - len(unassigned),
            unassigned_orders=len(unassigned),
            vehicle_count=len(result.routes),
            total_distance_meters=result.total_distance_meters,
            total_duration_seconds=result.total_duration_seconds,
        ),
        unassigned_orders=unassigned,
    )


def _plan_view(plan: DeliveryPlan) -> DeliveryPlanView:
    return DeliveryPlanView(
        id=plan.id,
        plan_code=plan.plan_code,
        plan_group_id=plan.plan_group_id,
        business_date=plan.business_date,
        version_no=plan.version_no,
        parent_plan_id=plan.parent_plan_id,
        status=plan.status.value,
        solver_engine=plan.solver_engine,
        validation_status=plan.validation_status.value,
        total_distance_meters=plan.total_distance_meters,
        total_duration_seconds=plan.total_duration_seconds,
        vehicle_count=plan.vehicle_count,
        assigned_order_count=plan.assigned_order_count,
        unassigned_order_count=plan.unassigned_order_count,
        validation_summary=plan.validation_summary,
        activated_at=plan.activated_at,
        superseded_at=plan.superseded_at,
        created_by=plan.created_by,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _plan_order_view(plan_order: DeliveryPlanOrder) -> PlanOrderMembershipView:
    return PlanOrderMembershipView(
        order_id=plan_order.order_id,
        order_code=plan_order.order.order_code,
        assignment_status=plan_order.assignment_status.value,
        vehicle_route_id=plan_order.vehicle_route_id,
        unassigned_reason_code=plan_order.unassigned_reason_code,
        unassigned_reason_detail=plan_order.unassigned_reason_detail,
    )


def _route_view(route: VehicleRoute) -> VehicleRouteView:
    return VehicleRouteView(
        id=route.id,
        delivery_plan_id=route.delivery_plan_id,
        route_no=route.route_no,
        vehicle_id=route.vehicle_id,
        driver_id=route.driver_id,
        vehicle_driver_assignment_id=route.vehicle_driver_assignment_id,
        start_location_id=route.start_location_id,
        end_location_id=route.end_location_id,
        status=route.status.value,
        planned_start_at=route.planned_start_at,
        planned_end_at=route.planned_end_at,
        actual_start_at=route.actual_start_at,
        actual_end_at=route.actual_end_at,
        distance_meters=route.distance_meters,
        duration_seconds=route.duration_seconds,
        vehicle_capacity_load_units_snapshot=(
            route.vehicle_capacity_load_units_snapshot
        ),
        route_geometry=route.route_geometry,
        route_metrics=route.route_metrics,
    )


def _stop_view(stop: RouteStop) -> RouteStopView:
    return RouteStopView(
        id=stop.id,
        vehicle_route_id=stop.vehicle_route_id,
        order_id=stop.order_id,
        location_id=stop.location_id,
        stop_type=stop.stop_type.value,
        sequence_no=stop.sequence_no,
        precedence_stop_id=stop.precedence_stop_id,
        source_incident_id=stop.source_incident_id,
        planned_arrival_at=stop.planned_arrival_at,
        planned_departure_at=stop.planned_departure_at,
        actual_arrival_at=stop.actual_arrival_at,
        actual_departure_at=stop.actual_departure_at,
        service_seconds=stop.service_seconds,
        time_window_start_at=stop.time_window_start_at,
        time_window_end_at=stop.time_window_end_at,
        demand_load_units_snapshot=stop.demand_load_units_snapshot,
        status=stop.status.value,
    )


class PlanningQueryService:
    def __init__(self, session: Session) -> None:
        self.repository = PlanRepository(session)

    def list_plans(
        self,
        *,
        page: int,
        page_size: int,
        business_date: date | None,
        status: DeliveryPlanStatus | None,
        plan_group_id: UUID | None,
    ) -> tuple[list[DeliveryPlanView], int]:
        plans = self.repository.list_plans(
            page=page,
            page_size=page_size,
            business_date=business_date,
            status=status,
            plan_group_id=plan_group_id,
        )
        total = self.repository.count_plans(
            business_date=business_date,
            status=status,
            plan_group_id=plan_group_id,
        )
        return [_plan_view(plan) for plan in plans], total

    def get_current_plan(self, business_date: date) -> DeliveryPlanView:
        plan = self.repository.get_current_plan(business_date)
        if plan is None:
            raise NotFound(
                code="CURRENT_PLAN_NOT_FOUND",
                message="Current delivery plan was not found",
            )
        return _plan_view(plan)

    def get_plan(self, plan_id: UUID) -> DeliveryPlanView:
        return _plan_view(self._require_plan(plan_id))

    def list_plan_orders(
        self,
        plan_id: UUID,
        *,
        page: int,
        page_size: int,
        assignment_status: PlanOrderAssignmentStatus | None,
    ) -> tuple[list[PlanOrderMembershipView], int]:
        self._require_plan(plan_id)
        memberships = self.repository.list_plan_orders(
            plan_id,
            page=page,
            page_size=page_size,
            assignment_status=assignment_status,
        )
        total = self.repository.count_plan_orders(
            plan_id,
            assignment_status=assignment_status,
        )
        return [_plan_order_view(item) for item in memberships], total

    def list_plan_routes(self, plan_id: UUID) -> list[VehicleRouteView]:
        self._require_plan(plan_id)
        return [
            _route_view(route) for route in self.repository.list_plan_routes(plan_id)
        ]

    def get_route(self, route_id: UUID) -> VehicleRouteView:
        return _route_view(self._require_route(route_id))

    def list_route_stops(self, route_id: UUID) -> list[RouteStopView]:
        self._require_route(route_id)
        return [
            _stop_view(stop) for stop in self.repository.list_route_stops(route_id)
        ]

    def get_stop(self, stop_id: UUID) -> RouteStopView:
        stop = self.repository.get_route_stop_by_id(stop_id)
        if stop is None:
            raise NotFound(
                code="ROUTE_STOP_NOT_FOUND",
                message="Route stop was not found",
            )
        return _stop_view(stop)

    def _require_plan(self, plan_id: UUID) -> DeliveryPlan:
        plan = self.repository.get_plan_by_id(plan_id)
        if plan is None:
            raise NotFound(
                code="DELIVERY_PLAN_NOT_FOUND",
                message="Delivery plan was not found",
            )
        return plan

    def _require_route(self, route_id: UUID) -> VehicleRoute:
        route = self.repository.get_route_by_id(route_id)
        if route is None:
            raise NotFound(
                code="VEHICLE_ROUTE_NOT_FOUND",
                message="Vehicle route was not found",
            )
        return route
