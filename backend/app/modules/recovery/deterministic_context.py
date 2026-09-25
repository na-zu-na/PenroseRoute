"""Materialize immutable facts used by deterministic recovery."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import BusinessError, Conflict, NotFound
from app.db.models.fleet import ResourceStatus
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
)
from app.db.models.recovery import (
    IncidentStatus,
    IncidentType,
    ReplanningScope,
)
from app.db.models.resources import OrderExecutionStatus
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.plan_repository import PlanRepository
from app.integrations.routing.contracts import RoutingLocation


@dataclass(frozen=True, slots=True)
class RecoveryOrderFact:
    order_id: UUID
    pickup_location_id: UUID
    delivery_location_id: UUID
    pickup_ready_at: datetime
    pickup_service_seconds: int
    delivery_window_start_at: datetime
    delivery_window_end_at: datetime
    delivery_service_seconds: int
    demand_load_units: int
    execution_status: str
    risk_status: str
    original_route_id: UUID | None
    handover_required: bool
    handover_location_id: UUID | None
    delivery_only: bool
    required_vehicle_id: UUID | None


@dataclass(frozen=True, slots=True)
class RecoveryVehicleFact:
    assignment_id: UUID
    vehicle_id: UUID
    driver_id: UUID
    capacity_load_units: int
    initial_load_load_units: int
    start_location_id: UUID
    available_from: datetime
    available_until: datetime


@dataclass(frozen=True, slots=True)
class StopSnapshot:
    id: UUID
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


@dataclass(frozen=True, slots=True)
class RouteSnapshot:
    id: UUID
    route_no: int
    vehicle_id: UUID
    driver_id: UUID
    assignment_id: UUID
    start_location_id: UUID
    end_location_id: UUID
    status: str
    planned_start_at: datetime
    planned_end_at: datetime
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    distance_meters: int
    duration_seconds: int
    capacity_load_units: int
    route_geometry: dict | None
    route_metrics: dict | None
    stops: tuple[StopSnapshot, ...]


@dataclass(frozen=True, slots=True)
class PlanOrderSnapshot:
    order_id: UUID
    assignment_status: str
    vehicle_route_id: UUID | None
    unassigned_reason_code: str | None
    unassigned_reason_detail: str | None


@dataclass(frozen=True, slots=True)
class ImpactSnapshot:
    order_id: UUID
    execution_status_snapshot: str
    risk_status_snapshot: str
    requires_replanning: bool
    handover_required: bool
    was_completed: bool
    impact_type: str


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    incident_id: UUID
    incident_type: str
    base_plan_id: UUID
    plan_group_id: UUID
    business_date: date
    base_version_no: int
    current_time: datetime
    scope: ReplanningScope
    affected_route_id: UUID | None
    target_orders: tuple[RecoveryOrderFact, ...]
    vehicles: tuple[RecoveryVehicleFact, ...]
    routes: tuple[RouteSnapshot, ...]
    plan_orders: tuple[PlanOrderSnapshot, ...]
    locations: tuple[RoutingLocation, ...]
    impact_snapshots: tuple[ImpactSnapshot, ...]
    incident_location_id: UUID | None
    delay_seconds: int | None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def materialize_recovery_context(
    session: Session,
    *,
    incident_id: UUID,
    scope: ReplanningScope,
    operational_time: datetime | None = None,
) -> RecoveryContext:
    incidents = IncidentRepository(session)
    plans = PlanRepository(session)
    fleet = FleetRepository(session)

    incident = incidents.get_incident_with_affected_orders(incident_id)
    if incident is None:
        raise NotFound(code="INCIDENT_NOT_FOUND", message="Incident was not found")
    if incident.status is IncidentStatus.RESOLVED:
        raise Conflict(
            code="RECOVERY_NOT_REQUIRED",
            message="Resolved incident does not require recovery",
        )
    current_time = max(incident.detected_at, operational_time or _now())
    affected = [item for item in incident.affected_orders if item.requires_replanning]
    if not affected:
        raise Conflict(
            code="RECOVERY_NOT_REQUIRED",
            message="Incident has no order requiring replanning",
        )
    if (
        incident.incident_type is IncidentType.VEHICLE_UNAVAILABLE
        and incident.incident_location is None
    ):
        raise BusinessError(
            code="RECOVERY_CONTEXT_INVALID",
            message="Vehicle incident is missing its breakdown location",
        )

    plan = plans.get_plan_for_recovery(incident.delivery_plan_id)
    if plan is None or plan.status is not DeliveryPlanStatus.CURRENT:
        raise Conflict(
            code="BASE_PLAN_NOT_CURRENT",
            message="Incident base delivery plan is no longer current",
        )

    routes_by_id = {route.id: route for route in plan.routes}
    affected_route_ids = {
        item.original_vehicle_route_id
        for item in affected
        if item.original_vehicle_route_id is not None
    }
    target_ids = {item.order_id for item in affected}
    target_ids.update(
        item.order_id
        for item in plan.plan_orders
        if item.vehicle_route_id in affected_route_ids
        and item.order.execution_status is not OrderExecutionStatus.COMPLETED
    )
    if scope is ReplanningScope.CROSS_ROUTE:
        active_route_ids = {
            route.id for route in plan.routes
            if route.vehicle.status is ResourceStatus.ACTIVE
        }
        target_ids.update(
            item.order_id
            for item in plan.plan_orders
            if item.vehicle_route_id in active_route_ids
            and item.order.execution_status is not OrderExecutionStatus.COMPLETED
        )
    if scope is ReplanningScope.ALL_REMAINING:
        target_ids.update(
            item.order_id
            for item in plan.plan_orders
            if item.order.execution_status is not OrderExecutionStatus.COMPLETED
        )

    impact_by_order = {item.order_id: item for item in incident.affected_orders}
    ordered_memberships = sorted(plan.plan_orders, key=lambda item: item.order_id)
    membership_by_order = {item.order_id: item for item in ordered_memberships}
    order_facts: list[RecoveryOrderFact] = []
    for order_id in sorted(target_ids):
        membership = membership_by_order.get(order_id)
        if membership is None:
            raise BusinessError(
                code="RECOVERY_CONTEXT_INVALID",
                message="Affected order is not a member of the base plan",
            )
        order = membership.order
        impact = impact_by_order.get(order_id)
        handover_required = bool(impact and impact.handover_required)
        delivery_only = (
            order.execution_status
            in (OrderExecutionStatus.PICKED_UP, OrderExecutionStatus.DELIVERING)
            and not handover_required
        )
        original_route = routes_by_id.get(membership.vehicle_route_id)
        if delivery_only and original_route is None:
            raise BusinessError(
                code="RECOVERY_CONTEXT_INVALID",
                message="Picked-up order has no executing vehicle route",
            )
        if handover_required and incident.incident_location_id is None:
            raise BusinessError(
                code="RECOVERY_CONTEXT_INVALID",
                message="Handover order is missing a breakdown location",
            )
        order_facts.append(
            RecoveryOrderFact(
                order_id=order.id,
                pickup_location_id=order.pickup_location_id,
                delivery_location_id=order.delivery_location_id,
                pickup_ready_at=(
                    incident.updated_ready_at
                    if incident.incident_type is IncidentType.MERCHANT_DELAY
                    and order.merchant_id == incident.merchant_id
                    else order.pickup_ready_at
                ),
                pickup_service_seconds=order.pickup_service_seconds,
                delivery_window_start_at=order.delivery_window_start_at,
                delivery_window_end_at=order.delivery_window_end_at,
                delivery_service_seconds=order.delivery_service_seconds,
                demand_load_units=order.demand_load_units,
                execution_status=order.execution_status.value,
                risk_status=order.risk_status.value,
                original_route_id=membership.vehicle_route_id,
                handover_required=handover_required,
                handover_location_id=(
                    incident.incident_location_id if handover_required else None
                ),
                delivery_only=delivery_only,
                required_vehicle_id=(
                    original_route.vehicle_id if delivery_only else None
                ),
            )
        )

    planning_end = max(
        order.delivery_window_end_at + timedelta(seconds=order.delivery_service_seconds)
        for order in order_facts
    )
    pairs = fleet.get_vehicle_driver_pairs_for_window(
        current_time, planning_end
    )
    affected_vehicle_ids = {
        routes_by_id[route_id].vehicle_id
        for route_id in affected_route_ids
        if route_id in routes_by_id
    }
    usable = []
    for assignment in pairs:
        vehicle = assignment.vehicle
        driver = assignment.driver
        if (
            vehicle.status is ResourceStatus.UNAVAILABLE
            or driver.status is ResourceStatus.UNAVAILABLE
        ):
            continue
        if scope is ReplanningScope.AFFECTED_ROUTE:
            if vehicle.id not in affected_vehicle_ids:
                continue
        usable.append(assignment)

    onboard_loads: dict[UUID, int] = {}
    for order in order_facts:
        if order.delivery_only and order.required_vehicle_id is not None:
            onboard_loads[order.required_vehicle_id] = (
                onboard_loads.get(order.required_vehicle_id, 0)
                + order.demand_load_units
            )
    vehicle_facts = tuple(
        RecoveryVehicleFact(
            assignment_id=item.id,
            vehicle_id=item.vehicle_id,
            driver_id=item.driver_id,
            capacity_load_units=item.vehicle.capacity_load_units,
            initial_load_load_units=onboard_loads.get(item.vehicle_id, 0),
            start_location_id=item.vehicle.current_location_id,
            available_from=max(item.assigned_from_at, current_time),
            available_until=item.assigned_until_at or planning_end,
        )
        for item in usable
        if (item.assigned_until_at or planning_end) >= current_time
    )

    route_snapshots = tuple(
        _route_snapshot(route)
        for route in sorted(plan.routes, key=lambda item: item.route_no)
    )
    plan_order_snapshots = tuple(
        PlanOrderSnapshot(
            order_id=item.order_id,
            assignment_status=item.assignment_status.value,
            vehicle_route_id=item.vehicle_route_id,
            unassigned_reason_code=item.unassigned_reason_code,
            unassigned_reason_detail=item.unassigned_reason_detail,
        )
        for item in ordered_memberships
    )

    locations: dict[UUID, RoutingLocation] = {}

    def add_location(location) -> None:
        locations[location.id] = RoutingLocation(
            location_id=location.id,
            latitude=float(Decimal(location.latitude)),
            longitude=float(Decimal(location.longitude)),
        )

    for item in ordered_memberships:
        if item.order_id in target_ids:
            add_location(item.order.pickup_location)
            add_location(item.order.delivery_location)
    if incident.incident_location is not None:
        add_location(incident.incident_location)
    for item in usable:
        add_location(item.vehicle.current_location)

    return RecoveryContext(
        incident_id=incident.id,
        incident_type=incident.incident_type.value,
        base_plan_id=plan.id,
        plan_group_id=plan.plan_group_id,
        business_date=plan.business_date,
        base_version_no=plan.version_no,
        current_time=current_time,
        scope=scope,
        affected_route_id=incident.vehicle_route_id,
        target_orders=tuple(order_facts),
        vehicles=vehicle_facts,
        routes=route_snapshots,
        plan_orders=plan_order_snapshots,
        locations=tuple(locations.values()),
        impact_snapshots=tuple(
            ImpactSnapshot(
                order_id=item.order_id,
                execution_status_snapshot=item.execution_status_snapshot.value,
                risk_status_snapshot=item.risk_status_snapshot.value,
                requires_replanning=item.requires_replanning,
                handover_required=item.handover_required,
                was_completed=item.was_completed,
                impact_type=item.impact_type.value,
            )
            for item in sorted(incident.affected_orders, key=lambda item: item.order_id)
        ),
        incident_location_id=incident.incident_location_id,
        delay_seconds=incident.delay_seconds,
    )


def _route_snapshot(route) -> RouteSnapshot:
    return RouteSnapshot(
        id=route.id,
        route_no=route.route_no,
        vehicle_id=route.vehicle_id,
        driver_id=route.driver_id,
        assignment_id=route.vehicle_driver_assignment_id,
        start_location_id=route.start_location_id,
        end_location_id=route.end_location_id,
        status=route.status.value,
        planned_start_at=route.planned_start_at,
        planned_end_at=route.planned_end_at,
        actual_start_at=route.actual_start_at,
        actual_end_at=route.actual_end_at,
        distance_meters=route.distance_meters,
        duration_seconds=route.duration_seconds,
        capacity_load_units=route.vehicle_capacity_load_units_snapshot,
        route_geometry=route.route_geometry,
        route_metrics=route.route_metrics,
        stops=tuple(
            StopSnapshot(
                id=stop.id,
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
            for stop in sorted(route.stops, key=lambda value: value.sequence_no)
        ),
    )
