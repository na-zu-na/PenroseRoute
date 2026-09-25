"""Atomic assembly of a validated Solver result into the plan aggregate."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from app.db.models import DeliveryPlan, DeliveryPlanOrder, RouteStop, VehicleRoute
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
    RouteStatus,
    StopStatus,
    StopType,
    ValidationStatus,
)
from app.db.repositories.plan_repository import PlanRepository
from app.integrations.optimization.contracts import (
    SolverResult,
    SolverStatus,
    SolverStopType,
)
from app.modules.planning.input_builder import PlanningFacts


class PlanFinalizer:
    def __init__(self, repository: PlanRepository) -> None:
        self.repository = repository

    def finalize(
        self,
        facts: PlanningFacts,
        result: SolverResult,
        *,
        activated_at: datetime,
    ) -> DeliveryPlan:
        if result.status is not SolverStatus.FEASIBLE:
            raise ValueError("only a feasible solver result can be finalized")

        previous = self.repository.get_latest_plan_for_business_date(
            facts.business_date
        )
        version_no = previous.version_no + 1 if previous is not None else 1
        plan = DeliveryPlan(
            id=uuid4(),
            plan_code=f"PLAN-{facts.business_date:%Y%m%d}-V{version_no}",
            plan_group_id=previous.plan_group_id if previous is not None else uuid4(),
            business_date=facts.business_date,
            version_no=version_no,
            parent_plan_id=previous.id if previous is not None else None,
            status=DeliveryPlanStatus.CURRENT,
            solver_engine="OR_TOOLS",
            validation_status=ValidationStatus.VALID,
            total_distance_meters=result.total_distance_meters,
            total_duration_seconds=result.total_duration_seconds,
            vehicle_count=len(result.routes),
            assigned_order_count=len(facts.orders) - len(result.unassigned_orders),
            unassigned_order_count=len(result.unassigned_orders),
            validation_summary={
                "feasible": True,
                "validation_issue_count": 0,
            },
            activated_at=activated_at,
            created_by="system:planning",
        )
        self.repository.add_delivery_plan(plan)

        orders = {order.order_id: order for order in facts.orders}
        pairs = {pair.vehicle_id: pair for pair in facts.vehicle_driver_pairs}
        route_by_order_id: dict[UUID, UUID] = {}
        for route_no, solver_route in enumerate(result.routes, start=1):
            pair = pairs[solver_route.vehicle_id]
            route_id = uuid4()
            route = VehicleRoute(
                id=route_id,
                delivery_plan_id=plan.id,
                route_no=route_no,
                vehicle_id=pair.vehicle_id,
                driver_id=pair.driver_id,
                vehicle_driver_assignment_id=pair.assignment_id,
                start_location_id=pair.start_location_id,
                end_location_id=solver_route.stops[-1].location_id,
                status=RouteStatus.PLANNED,
                planned_start_at=facts.current_time
                + timedelta(seconds=max(0, int((pair.assigned_from_at - facts.current_time).total_seconds()))),
                planned_end_at=facts.current_time
                + timedelta(seconds=solver_route.stops[-1].departure_time_seconds),
                distance_meters=solver_route.distance_meters,
                duration_seconds=solver_route.duration_seconds,
                vehicle_capacity_load_units_snapshot=pair.capacity_load_units,
                route_geometry=None,
                route_metrics={"stop_count": len(solver_route.stops)},
            )
            self.repository.add_vehicle_route(route)

            origin_stop_ids: dict[UUID, UUID] = {}
            for solver_stop in solver_route.stops:
                order = orders[solver_stop.order_id]
                stop_id = uuid4()
                stop_type = StopType(solver_stop.stop_type.value)
                stop = RouteStop(
                    id=stop_id,
                    vehicle_route_id=route_id,
                    order_id=solver_stop.order_id,
                    location_id=solver_stop.location_id,
                    stop_type=stop_type,
                    sequence_no=solver_stop.sequence_no,
                    precedence_stop_id=(
                        origin_stop_ids[solver_stop.order_id]
                        if solver_stop.stop_type is SolverStopType.DELIVERY
                        else None
                    ),
                    source_incident_id=None,
                    planned_arrival_at=facts.current_time
                    + timedelta(seconds=solver_stop.arrival_time_seconds),
                    planned_departure_at=facts.current_time
                    + timedelta(seconds=solver_stop.departure_time_seconds),
                    service_seconds=solver_stop.service_duration_seconds,
                    time_window_start_at=(
                        order.delivery_window_start_at
                        if solver_stop.stop_type is SolverStopType.DELIVERY
                        else order.pickup_ready_at
                    ),
                    time_window_end_at=(
                        order.delivery_window_end_at
                        if solver_stop.stop_type is SolverStopType.DELIVERY
                        else None
                    ),
                    demand_load_units_snapshot=order.demand_load_units,
                    status=StopStatus.PLANNED,
                )
                self.repository.add_route_stop(stop)
                if solver_stop.stop_type is not SolverStopType.DELIVERY:
                    origin_stop_ids[solver_stop.order_id] = stop_id
                route_by_order_id[solver_stop.order_id] = route_id

        unassigned = {item.order_id: item for item in result.unassigned_orders}
        for order in facts.orders:
            if order.order_id in route_by_order_id:
                plan_order = DeliveryPlanOrder(
                    delivery_plan_id=plan.id,
                    order_id=order.order_id,
                    assignment_status=PlanOrderAssignmentStatus.ASSIGNED,
                    vehicle_route_id=route_by_order_id[order.order_id],
                    unassigned_reason_code=None,
                    unassigned_reason_detail=None,
                )
            else:
                reason = unassigned[order.order_id]
                plan_order = DeliveryPlanOrder(
                    delivery_plan_id=plan.id,
                    order_id=order.order_id,
                    assignment_status=PlanOrderAssignmentStatus.UNASSIGNED,
                    vehicle_route_id=None,
                    unassigned_reason_code=reason.reason_code,
                    unassigned_reason_detail=reason.reason_detail,
                )
            self.repository.add_delivery_plan_order(plan_order)

        self.repository.flush()
        return plan
