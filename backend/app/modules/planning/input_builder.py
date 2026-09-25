"""Materialized normal-planning facts and Solver contract construction."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from app.db.models import Order, VehicleDriverAssignment
from app.db.models.fleet import ResourceStatus
from app.integrations.optimization.contracts import (
    SolverInput,
    SolverOrder,
    SolverVehicle,
)
from app.integrations.routing.contracts import RoutingLocation, RoutingMatrix


@dataclass(frozen=True, slots=True)
class PlanningOrderFact:
    order_id: UUID
    order_code: str
    business_date: date
    merchant_id: UUID
    customer_id: UUID
    pickup_location_id: UUID
    delivery_location_id: UUID
    pickup_ready_at: datetime
    pickup_service_seconds: int
    delivery_window_start_at: datetime
    delivery_window_end_at: datetime
    delivery_service_seconds: int
    demand_load_units: int
    execution_status: str


@dataclass(frozen=True, slots=True)
class VehicleDriverPairFact:
    assignment_id: UUID
    vehicle_id: UUID
    driver_id: UUID
    capacity_load_units: int
    start_location_id: UUID
    assigned_from_at: datetime
    assigned_until_at: datetime | None
    assignment_status: str
    vehicle_status: str
    driver_status: str


@dataclass(frozen=True, slots=True)
class PlanningFacts:
    business_date: date
    current_time: datetime
    orders: tuple[PlanningOrderFact, ...]
    vehicle_driver_pairs: tuple[VehicleDriverPairFact, ...]
    locations: tuple[RoutingLocation, ...]


def materialize_planning_facts(
    business_date: date,
    orders: list[Order],
    assignments: list[VehicleDriverAssignment],
) -> PlanningFacts:
    order_facts = tuple(
        PlanningOrderFact(
            order_id=order.id,
            order_code=order.order_code,
            business_date=order.business_date,
            merchant_id=order.merchant_id,
            customer_id=order.customer_id,
            pickup_location_id=order.pickup_location_id,
            delivery_location_id=order.delivery_location_id,
            pickup_ready_at=order.pickup_ready_at,
            pickup_service_seconds=order.pickup_service_seconds,
            delivery_window_start_at=order.delivery_window_start_at,
            delivery_window_end_at=order.delivery_window_end_at,
            delivery_service_seconds=order.delivery_service_seconds,
            demand_load_units=order.demand_load_units,
            execution_status=order.execution_status.value,
        )
        for order in orders
    )
    usable_assignments = [
        assignment
        for assignment in assignments
        if assignment.vehicle.status is ResourceStatus.AVAILABLE
        and assignment.driver.status is ResourceStatus.AVAILABLE
    ]
    pair_facts = tuple(
        VehicleDriverPairFact(
            assignment_id=assignment.id,
            vehicle_id=assignment.vehicle_id,
            driver_id=assignment.driver_id,
            capacity_load_units=assignment.vehicle.capacity_load_units,
            start_location_id=assignment.vehicle.current_location_id,
            assigned_from_at=assignment.assigned_from_at,
            assigned_until_at=assignment.assigned_until_at,
            assignment_status=assignment.status.value,
            vehicle_status=assignment.vehicle.status.value,
            driver_status=assignment.driver.status.value,
        )
        for assignment in usable_assignments
    )

    locations_by_id: dict[UUID, RoutingLocation] = {}

    def add_location(location_id: UUID, latitude: Decimal, longitude: Decimal) -> None:
        locations_by_id[location_id] = RoutingLocation(
            location_id=location_id,
            latitude=float(latitude),
            longitude=float(longitude),
        )

    for order in orders:
        add_location(
            order.pickup_location_id,
            order.pickup_location.latitude,
            order.pickup_location.longitude,
        )
        add_location(
            order.delivery_location_id,
            order.delivery_location.latitude,
            order.delivery_location.longitude,
        )
    for assignment in usable_assignments:
        location = assignment.vehicle.current_location
        add_location(location.id, location.latitude, location.longitude)

    time_candidates = [fact.pickup_ready_at for fact in order_facts]
    time_candidates.extend(
        fact.delivery_window_start_at for fact in order_facts
    )
    time_candidates.extend(pair.assigned_from_at for pair in pair_facts)
    current_time = (
        min(time_candidates)
        if time_candidates
        else datetime.combine(business_date, time.min, timezone.utc)
    )
    return PlanningFacts(
        business_date=business_date,
        current_time=current_time,
        orders=order_facts,
        vehicle_driver_pairs=pair_facts,
        locations=tuple(locations_by_id.values()),
    )


def build_solver_input(
    facts: PlanningFacts,
    matrix: RoutingMatrix,
) -> SolverInput:
    def seconds_from_current(value: datetime) -> int:
        return int((value - facts.current_time).total_seconds())

    planning_end = max(
        (
            order.delivery_window_end_at
            + timedelta(seconds=order.delivery_service_seconds)
            for order in facts.orders
        ),
        default=facts.current_time,
    )
    return SolverInput(
        business_date=facts.business_date,
        current_time=facts.current_time,
        orders=tuple(
            SolverOrder(
                order_id=order.order_id,
                pickup_location_id=order.pickup_location_id,
                handover_location_id=None,
                delivery_location_id=order.delivery_location_id,
                ready_time_seconds=seconds_from_current(order.pickup_ready_at),
                delivery_window_start_seconds=seconds_from_current(
                    order.delivery_window_start_at
                ),
                delivery_window_end_seconds=seconds_from_current(
                    order.delivery_window_end_at
                ),
                pickup_service_seconds=order.pickup_service_seconds,
                handover_service_seconds=0,
                delivery_service_seconds=order.delivery_service_seconds,
                demand_load_units=order.demand_load_units,
            )
            for order in facts.orders
        ),
        vehicles=tuple(
            SolverVehicle(
                vehicle_id=pair.vehicle_id,
                capacity_load_units=pair.capacity_load_units,
                start_location_id=pair.start_location_id,
                available_from_seconds=seconds_from_current(pair.assigned_from_at),
                available_until_seconds=seconds_from_current(
                    pair.assigned_until_at or planning_end
                ),
            )
            for pair in facts.vehicle_driver_pairs
        ),
        frozen_tasks=(),
        recovery_scope=None,
        location_ids=matrix.location_ids,
        distance_matrix_meters=matrix.distance_matrix_meters,
        duration_matrix_seconds=matrix.duration_matrix_seconds,
    )
