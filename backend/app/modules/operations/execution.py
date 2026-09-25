"""Transactional route-stop execution state machine."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.db.models.fleet import AssignmentStatus, ResourceStatus
from app.db.models.planning import (
    DeliveryPlanStatus,
    RouteStatus,
    RouteStop,
    StopStatus,
    StopType,
)
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.operations.risk import RiskService


@dataclass(frozen=True)
class StopExecutionResult:
    stop: RouteStop
    order_execution_status: str
    order_risk_status: str
    route_status: str
    vehicle_status: str
    driver_status: str


class StopExecutionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.plans = PlanRepository(session)
        self.resources = ResourceRepository(session)
        self.fleet = FleetRepository(session)
        self.risk = RiskService(
            threshold_seconds=get_settings().at_risk_threshold_seconds
        )

    def arrive(
        self, stop_id: UUID, occurred_at: datetime | None = None
    ) -> StopExecutionResult:
        return self._transition(
            stop_id=stop_id,
            expected=StopStatus.PLANNED,
            target=StopStatus.ARRIVED,
            occurred_at=occurred_at,
        )

    def start_service(
        self, stop_id: UUID, occurred_at: datetime | None = None
    ) -> StopExecutionResult:
        return self._transition(
            stop_id=stop_id,
            expected=StopStatus.ARRIVED,
            target=StopStatus.IN_SERVICE,
            occurred_at=occurred_at,
        )

    def complete(
        self, stop_id: UUID, occurred_at: datetime | None = None
    ) -> StopExecutionResult:
        return self._transition(
            stop_id=stop_id,
            expected=StopStatus.IN_SERVICE,
            target=StopStatus.COMPLETED,
            occurred_at=occurred_at,
        )

    def _transition(
        self,
        *,
        stop_id: UUID,
        expected: StopStatus,
        target: StopStatus,
        occurred_at: datetime | None,
    ) -> StopExecutionResult:
        event_time = occurred_at or datetime.now(timezone.utc)
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            raise Conflict(
                code="INVALID_EVENT_TIME",
                message="occurred_at must include a timezone offset",
            )

        with self.session.begin():
            stop = self.plans.lock_route_stop_by_id(stop_id)
            if stop is None:
                raise NotFound(
                    code="STOP_NOT_FOUND",
                    message=f"Route stop {stop_id} was not found",
                )
            route = self.plans.lock_route_by_id(stop.vehicle_route_id)
            if route is None:
                raise NotFound(
                    code="ROUTE_NOT_FOUND",
                    message=f"Vehicle route {stop.vehicle_route_id} was not found",
                )
            plan = self.plans.lock_plan_by_id(route.delivery_plan_id)
            if plan is None or plan.status is not DeliveryPlanStatus.CURRENT:
                raise Conflict(
                    code="BASE_PLAN_NOT_CURRENT",
                    message="Stop actions are allowed only on the current plan",
                )
            order = self.resources.lock_order_by_id(stop.order_id)
            vehicle = self.fleet.lock_vehicle_by_id(route.vehicle_id)
            driver = self.fleet.lock_driver_by_id(route.driver_id)
            assignment = self.fleet.lock_assignment_by_id(
                route.vehicle_driver_assignment_id
            )
            if order is None or vehicle is None or driver is None or assignment is None:
                raise Conflict(
                    code="EXECUTION_RESOURCE_MISSING",
                    message="The stop execution resources are incomplete",
                )
            stops = self.plans.list_route_stops_with_orders(route.id)

            if stop.status is target:
                return self._result(stop, order, route, vehicle, driver)
            if stop.status is not expected:
                raise Conflict(
                    code="INVALID_STOP_STATE_TRANSITION",
                    message=(
                        f"Stop cannot transition from {stop.status} to {target}"
                    ),
                )
            self._validate_sequence(stop, stops)
            self._validate_event_time(stop, stops, event_time, target)
            self._activate_route(
                route=route,
                vehicle=vehicle,
                driver=driver,
                assignment=assignment,
                event_time=event_time,
            )

            stop.status = target
            if target is StopStatus.ARRIVED:
                stop.actual_arrival_at = event_time
            elif target is StopStatus.COMPLETED:
                stop.actual_departure_at = event_time

            self._advance_order(stop, target)
            self.risk.recalculate_route(stops=stops, current_time=event_time)

            if target is StopStatus.COMPLETED and all(
                item.status is StopStatus.COMPLETED for item in stops
            ):
                route.status = RouteStatus.COMPLETED
                route.actual_end_at = event_time
                if vehicle.status is not ResourceStatus.UNAVAILABLE:
                    vehicle.status = ResourceStatus.AVAILABLE
                if driver.status is not ResourceStatus.UNAVAILABLE:
                    driver.status = ResourceStatus.AVAILABLE
                assignment.status = AssignmentStatus.ENDED
                assignment.ended_at = event_time

            self.plans.flush()
            return self._result(stop, order, route, vehicle, driver)

    @staticmethod
    def _validate_sequence(stop: RouteStop, stops: list[RouteStop]) -> None:
        if any(
            item.sequence_no < stop.sequence_no
            and item.status is not StopStatus.COMPLETED
            for item in stops
        ):
            raise Conflict(
                code="INVALID_STOP_STATE_TRANSITION",
                message="All earlier route stops must be completed first",
            )

    @staticmethod
    def _validate_event_time(
        stop: RouteStop,
        stops: list[RouteStop],
        event_time: datetime,
        target: StopStatus,
    ) -> None:
        previous = next(
            (
                item
                for item in reversed(stops)
                if item.sequence_no < stop.sequence_no
            ),
            None,
        )
        if (
            previous is not None
            and previous.actual_departure_at is not None
            and event_time < previous.actual_departure_at
        ):
            raise Conflict(
                code="INVALID_EVENT_TIME",
                message="occurred_at cannot precede the previous stop completion",
            )
        if (
            target is StopStatus.COMPLETED
            and stop.actual_arrival_at is not None
            and event_time < stop.actual_arrival_at
        ):
            raise Conflict(
                code="INVALID_EVENT_TIME",
                message="Stop completion cannot precede arrival",
            )

    @staticmethod
    def _activate_route(*, route, vehicle, driver, assignment, event_time) -> None:
        if route.status is RouteStatus.CANCELLED:
            raise Conflict(
                code="INVALID_STOP_STATE_TRANSITION",
                message="A cancelled route cannot be executed",
            )
        if route.status is RouteStatus.COMPLETED:
            raise Conflict(
                code="INVALID_STOP_STATE_TRANSITION",
                message="A completed route cannot be executed",
            )
        if vehicle.status is ResourceStatus.UNAVAILABLE:
            raise Conflict(
                code="VEHICLE_NOT_AVAILABLE",
                message="The route vehicle is unavailable",
            )
        if driver.status is ResourceStatus.UNAVAILABLE:
            raise Conflict(
                code="DRIVER_NOT_AVAILABLE",
                message="The route driver is unavailable",
            )
        if assignment.status in (AssignmentStatus.ENDED, AssignmentStatus.CANCELLED):
            raise Conflict(
                code="INVALID_ASSIGNMENT_STATE_TRANSITION",
                message="The vehicle-driver assignment cannot execute this route",
            )
        if route.status is RouteStatus.PLANNED:
            route.status = RouteStatus.ACTIVE
            route.actual_start_at = event_time
            vehicle.status = ResourceStatus.ACTIVE
            driver.status = ResourceStatus.ACTIVE
            assignment.status = AssignmentStatus.ACTIVE
            assignment.activated_at = assignment.activated_at or event_time

    @staticmethod
    def _advance_order(stop: RouteStop, target: StopStatus) -> None:
        order = stop.order
        desired = None
        if stop.stop_type is StopType.PICKUP:
            if target is StopStatus.ARRIVED:
                desired = OrderExecutionStatus.PICKUP_IN_PROGRESS
            elif target is StopStatus.COMPLETED:
                desired = OrderExecutionStatus.PICKED_UP
        elif stop.stop_type in (StopType.DELIVERY, StopType.HANDOVER):
            if target in (StopStatus.ARRIVED, StopStatus.IN_SERVICE):
                desired = OrderExecutionStatus.DELIVERING
            elif target is StopStatus.COMPLETED:
                desired = (
                    OrderExecutionStatus.COMPLETED
                    if stop.stop_type is StopType.DELIVERY
                    else OrderExecutionStatus.DELIVERING
                )
        if desired is None:
            return
        ranks = {
            OrderExecutionStatus.PLANNED: 0,
            OrderExecutionStatus.PICKUP_IN_PROGRESS: 1,
            OrderExecutionStatus.PICKED_UP: 2,
            OrderExecutionStatus.DELIVERING: 3,
            OrderExecutionStatus.COMPLETED: 4,
        }
        if ranks[desired] > ranks[order.execution_status]:
            order.execution_status = desired
        if desired is OrderExecutionStatus.COMPLETED:
            order.risk_status = OrderRiskStatus.NORMAL

    @staticmethod
    def _result(stop, order, route, vehicle, driver) -> StopExecutionResult:
        return StopExecutionResult(
            stop=stop,
            order_execution_status=order.execution_status,
            order_risk_status=order.risk_status,
            route_status=route.status,
            vehicle_status=vehicle.status,
            driver_status=driver.status,
        )
