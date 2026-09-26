"""Read-only comparison of one Recovery Attempt's Base and Candidate snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import BusinessError, NotFound
from app.db.models.planning import DeliveryPlanStatus
from app.db.models.recovery import RecoveryPlanStatus
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.recovery_repository import RecoveryRepository


@dataclass(frozen=True, slots=True)
class OrderComparisonView:
    order_id: UUID
    base_assignment_status: str | None
    candidate_assignment_status: str | None
    base_vehicle_id: UUID | None
    candidate_vehicle_id: UUID | None
    base_unassigned_reason_code: str | None
    candidate_unassigned_reason_code: str | None
    base_unassigned_reason_detail: str | None
    candidate_unassigned_reason_detail: str | None
    assignment_changed: bool
    route_task_changed: bool
    base_delivery_eta: datetime | None
    candidate_delivery_eta: datetime | None
    eta_delta_seconds: int | None
    eta_basis: str | None
    eta_unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class StopComparisonView:
    order_id: UUID
    stop_type: str
    source_incident_id: UUID | None
    change_type: str
    base_vehicle_id: UUID | None
    candidate_vehicle_id: UUID | None
    base_sequence_no: int | None
    candidate_sequence_no: int | None
    base_location_id: UUID | None
    candidate_location_id: UUID | None
    base_status: str | None
    candidate_status: str | None
    base_planned_arrival_at: datetime | None
    candidate_planned_arrival_at: datetime | None
    base_planned_departure_at: datetime | None
    candidate_planned_departure_at: datetime | None
    base_actual_arrival_at: datetime | None
    candidate_actual_arrival_at: datetime | None
    base_actual_departure_at: datetime | None
    candidate_actual_departure_at: datetime | None
    base_precedence_stop_type: str | None
    candidate_precedence_stop_type: str | None
    base_precedence_location_id: UUID | None
    candidate_precedence_location_id: UUID | None
    base_precedence_source_incident_id: UUID | None
    candidate_precedence_source_incident_id: UUID | None


@dataclass(frozen=True, slots=True)
class RouteComparisonView:
    vehicle_id: UUID
    base_route_id: UUID | None
    candidate_route_id: UUID | None
    base_stop_count: int
    candidate_stop_count: int


@dataclass(frozen=True, slots=True)
class RemainingMetricsView:
    base_distance_meters: int | None
    candidate_distance_meters: int | None
    delta_distance_meters: int | None
    base_duration_seconds: int | None
    candidate_duration_seconds: int | None
    delta_duration_seconds: int | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class PlanComparisonView:
    recovery_plan_id: UUID
    base_plan_id: UUID
    candidate_plan_id: UUID
    business_date: date
    comparison_at: datetime
    comparison_time_basis: str
    base_plan_status: str
    candidate_plan_status: str
    reviewable: bool
    orders: tuple[OrderComparisonView, ...]
    stop_changes: tuple[StopComparisonView, ...]
    route_changes: tuple[RouteComparisonView, ...]
    reassigned_order_count: int
    affected_vehicle_ids: tuple[UUID, ...]
    frozen_completed_order_ids: tuple[UUID, ...]
    remaining_metrics: RemainingMetricsView


def _value(value: object) -> str:
    """StrEnum and plain strings both describe the stored snapshot value."""
    return str(value)


def _action_key(stop) -> tuple[UUID, str, UUID | None]:
    kind = _value(stop.stop_type)
    return (stop.order_id, kind, stop.source_incident_id if kind == "HANDOVER" else None)


def _plan_actions(plan):
    actions = {}
    routes = {route.id: route for route in plan.routes}
    for route in plan.routes:
        for stop in route.stops:
            key = _action_key(stop)
            if key in actions:
                raise BusinessError(code="PLAN_COMPARISON_INVALID", message="Duplicate business stop action")
            actions[key] = (stop, route.vehicle_id)
    return actions, routes


def _predecessor(stop, actions):
    if stop.precedence_stop_id is None:
        return None
    for prior, _ in actions.values():
        if prior.id == stop.precedence_stop_id:
            return (_action_key(prior), prior.location_id)
    return None


def _predecessor_fields(stop, actions):
    predecessor = _predecessor(stop, actions) if stop else None
    if predecessor is None:
        return (None, None, None)
    action, location_id = predecessor
    return (action[1], location_id, action[2])


def _stop_signature(stop, vehicle_id, actions):
    if _value(stop.status) == "COMPLETED":
        # Reindexed copied prefixes are not changes to irreversible execution facts.
        return (
            vehicle_id, stop.location_id, "COMPLETED",
            stop.actual_arrival_at, stop.actual_departure_at,
            _predecessor(stop, actions),
        )
    return (
        vehicle_id, stop.sequence_no, stop.location_id,
        stop.planned_arrival_at, stop.planned_departure_at,
        _value(stop.status), stop.actual_arrival_at, stop.actual_departure_at,
        _predecessor(stop, actions),
    )


def _route_signature(route, actions):
    return tuple(
        (_action_key(stop), *_stop_signature(stop, route.vehicle_id, actions))
        for stop in sorted(route.stops, key=lambda item: item.sequence_no)
    )


def compare_plan_snapshots(*, recovery_plan_id: UUID, comparison_at: datetime,
                           base, candidate, reviewable: bool) -> PlanComparisonView:
    """Compare persisted plan membership and stops without consulting live resources."""
    base_actions, base_routes = _plan_actions(base)
    candidate_actions, candidate_routes = _plan_actions(candidate)
    base_members = {item.order_id: item for item in base.plan_orders}
    candidate_members = {item.order_id: item for item in candidate.plan_orders}

    stop_changes = []
    for key in sorted(base_actions.keys() | candidate_actions.keys(), key=lambda value: (str(value[0]), value[1], str(value[2]))):
        before = base_actions.get(key)
        after = candidate_actions.get(key)
        if before and after and _stop_signature(*before, base_actions) == _stop_signature(*after, candidate_actions):
            continue
        old_stop, old_vehicle = before if before else (None, None)
        new_stop, new_vehicle = after if after else (None, None)
        old_predecessor = _predecessor_fields(old_stop, base_actions)
        new_predecessor = _predecessor_fields(new_stop, candidate_actions)
        stop_changes.append(StopComparisonView(
            order_id=key[0], stop_type=key[1], source_incident_id=key[2],
            change_type="MODIFIED" if before and after else "REMOVED" if before else "ADDED",
            base_vehicle_id=old_vehicle, candidate_vehicle_id=new_vehicle,
            base_sequence_no=old_stop.sequence_no if old_stop else None,
            candidate_sequence_no=new_stop.sequence_no if new_stop else None,
            base_location_id=old_stop.location_id if old_stop else None,
            candidate_location_id=new_stop.location_id if new_stop else None,
            base_status=_value(old_stop.status) if old_stop else None,
            candidate_status=_value(new_stop.status) if new_stop else None,
            base_planned_arrival_at=old_stop.planned_arrival_at if old_stop else None,
            candidate_planned_arrival_at=new_stop.planned_arrival_at if new_stop else None,
            base_planned_departure_at=old_stop.planned_departure_at if old_stop else None,
            candidate_planned_departure_at=new_stop.planned_departure_at if new_stop else None,
            base_actual_arrival_at=old_stop.actual_arrival_at if old_stop else None,
            candidate_actual_arrival_at=new_stop.actual_arrival_at if new_stop else None,
            base_actual_departure_at=old_stop.actual_departure_at if old_stop else None,
            candidate_actual_departure_at=new_stop.actual_departure_at if new_stop else None,
            base_precedence_stop_type=old_predecessor[0],
            candidate_precedence_stop_type=new_predecessor[0],
            base_precedence_location_id=old_predecessor[1],
            candidate_precedence_location_id=new_predecessor[1],
            base_precedence_source_incident_id=old_predecessor[2],
            candidate_precedence_source_incident_id=new_predecessor[2],
        ))

    changed_stop_orders = {item.order_id for item in stop_changes}
    orders = []
    for order_id in sorted(base_members.keys() | candidate_members.keys()):
        before, after = base_members.get(order_id), candidate_members.get(order_id)
        old_route = base_routes.get(before.vehicle_route_id) if before else None
        new_route = candidate_routes.get(after.vehicle_route_id) if after else None
        old_vehicle = old_route.vehicle_id if old_route else None
        new_vehicle = new_route.vehicle_id if new_route else None
        old_delivery = base_actions.get((order_id, "DELIVERY", None))
        new_delivery = candidate_actions.get((order_id, "DELIVERY", None))
        if (
            (old_delivery and _value(old_delivery[0].status) == "COMPLETED")
            or (new_delivery and _value(new_delivery[0].status) == "COMPLETED")
        ):
            old_eta = new_eta = delta = basis = None
            reason = "COMPLETED_DELIVERY"
        else:
            old_eta = (old_delivery[0].planned_arrival_at if old_vehicle is not None
                       and old_delivery and old_delivery[1] == old_vehicle else None)
            new_eta = (new_delivery[0].planned_arrival_at if new_vehicle is not None
                       and new_delivery and new_delivery[1] == new_vehicle else None)
            delta = int((new_eta - old_eta).total_seconds()) if old_eta and new_eta else None
            basis = "PLANNED_DELIVERY_ARRIVAL" if old_eta or new_eta else None
            reason = None if delta is not None else "DELIVERY_STOP_NOT_AVAILABLE"
        before_status = _value(before.assignment_status) if before else None
        after_status = _value(after.assignment_status) if after else None
        before_reason = before.unassigned_reason_code if before else None
        after_reason = after.unassigned_reason_code if after else None
        before_detail = before.unassigned_reason_detail if before else None
        after_detail = after.unassigned_reason_detail if after else None
        orders.append(OrderComparisonView(
            order_id=order_id,
            base_assignment_status=before_status, candidate_assignment_status=after_status,
            base_vehicle_id=old_vehicle, candidate_vehicle_id=new_vehicle,
            base_unassigned_reason_code=before_reason, candidate_unassigned_reason_code=after_reason,
            base_unassigned_reason_detail=before_detail, candidate_unassigned_reason_detail=after_detail,
            assignment_changed=(before_status, old_vehicle, before_reason, before_detail)
            != (after_status, new_vehicle, after_reason, after_detail),
            route_task_changed=order_id in changed_stop_orders,
            base_delivery_eta=old_eta, candidate_delivery_eta=new_eta,
            eta_delta_seconds=delta, eta_basis=basis, eta_unavailable_reason=reason,
        ))

    base_by_vehicle = {route.vehicle_id: route for route in base.routes}
    candidate_by_vehicle = {route.vehicle_id: route for route in candidate.routes}
    route_changes = tuple(
        RouteComparisonView(
            vehicle_id=vehicle_id,
            base_route_id=old.id if old else None,
            candidate_route_id=new.id if new else None,
            base_stop_count=len(old.stops) if old else 0,
            candidate_stop_count=len(new.stops) if new else 0,
        )
        for vehicle_id in sorted(base_by_vehicle.keys() | candidate_by_vehicle.keys())
        for old, new in [(base_by_vehicle.get(vehicle_id), candidate_by_vehicle.get(vehicle_id))]
        if old is None or new is None or _route_signature(old, base_actions) != _route_signature(new, candidate_actions)
    )
    frozen = tuple(sorted(
        order_id for (order_id, kind, _), (old, _) in base_actions.items()
        if kind == "DELIVERY" and _value(old.status) == "COMPLETED"
        and order_id not in changed_stop_orders
        and (new_entry := candidate_actions.get((order_id, kind, None))) is not None
        and _value(new_entry[0].status) == "COMPLETED"
    ))
    affected = {item.vehicle_id for item in route_changes}
    for item in orders:
        if item.assignment_changed:
            affected.update(vehicle for vehicle in (item.base_vehicle_id, item.candidate_vehicle_id) if vehicle)
    return PlanComparisonView(
        recovery_plan_id=recovery_plan_id,
        base_plan_id=base.id, candidate_plan_id=candidate.id,
        business_date=base.business_date, comparison_at=comparison_at,
        comparison_time_basis="ATTEMPT_RECORDED_AT",
        base_plan_status=_value(base.status), candidate_plan_status=_value(candidate.status),
        reviewable=reviewable, orders=tuple(orders), stop_changes=tuple(stop_changes),
        route_changes=route_changes,
        reassigned_order_count=sum(
            item.base_vehicle_id is not None and item.candidate_vehicle_id is not None
            and item.base_vehicle_id != item.candidate_vehicle_id for item in orders
        ),
        affected_vehicle_ids=tuple(sorted(affected)),
        frozen_completed_order_ids=frozen,
        remaining_metrics=RemainingMetricsView(
            base_distance_meters=None, candidate_distance_meters=None,
            delta_distance_meters=None, base_duration_seconds=None,
            candidate_duration_seconds=None, delta_duration_seconds=None,
            reason="NO_COMPARABLE_REMAINDER_SNAPSHOT",
        ),
    )


class PlanComparisonService:
    def __init__(self, session: Session) -> None:
        self.plans = PlanRepository(session)
        self.recoveries = RecoveryRepository(session)

    def compare_recovery(self, recovery_plan_id: UUID) -> PlanComparisonView:
        attempt = self.recoveries.get_recovery_plan_by_id(recovery_plan_id)
        if attempt is None:
            raise NotFound(code="RECOVERY_NOT_FOUND", message="Recovery attempt was not found")
        if attempt.candidate_delivery_plan_id is None:
            raise NotFound(code="CANDIDATE_PLAN_NOT_FOUND", message="Recovery candidate was not found")
        base = self.plans.get_plan_with_routes(attempt.base_delivery_plan_id)
        candidate = self.plans.get_plan_with_routes(attempt.candidate_delivery_plan_id)
        if candidate is None:
            raise NotFound(code="CANDIDATE_PLAN_NOT_FOUND", message="Recovery candidate was not found")
        if (
            base is None or candidate.parent_plan_id != base.id
            or candidate.business_date != base.business_date
            or candidate.plan_group_id != base.plan_group_id
        ):
            raise BusinessError(code="PLAN_COMPARISON_INVALID", message="Recovery Base/Candidate relationship is invalid")
        return compare_plan_snapshots(
            recovery_plan_id=attempt.id, comparison_at=attempt.created_at,
            base=base, candidate=candidate,
            reviewable=(attempt.status == RecoveryPlanStatus.PENDING_REVIEW
                        and base.status == DeliveryPlanStatus.CURRENT
                        and candidate.status == DeliveryPlanStatus.CANDIDATE),
        )
