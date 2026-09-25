"""Current-plan operational read models."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFound
from app.db.models.fleet import ResourceStatus
from app.db.models.planning import (
    PlanOrderAssignmentStatus,
    RouteStatus,
    StopStatus,
    StopType,
    VehicleRoute,
)
from app.db.models.recovery import IncidentStatus, RecoveryPlanStatus
from app.db.models.resources import (
    MerchantPreparationStatus,
    OrderExecutionStatus,
    OrderRiskStatus,
)
from app.db.repositories.plan_repository import PlanRepository
from app.modules.operations.risk import RiskService


@dataclass(frozen=True)
class CurrentPlanView:
    delivery_plan_id: UUID
    plan_code: str
    version_no: int


@dataclass(frozen=True)
class CountView:
    total: int
    completed: int = 0
    in_progress: int = 0
    at_risk: int = 0
    available: int = 0
    active: int = 0
    unavailable: int = 0
    ready: int = 0
    preparing: int = 0
    delayed: int = 0


@dataclass(frozen=True)
class OperationsDashboardView:
    current_plan: CurrentPlanView
    orders: CountView
    vehicles: CountView
    drivers: CountView
    merchants: CountView
    open_incidents: int
    pending_recovery_reviews: int
    calculated_at: datetime


@dataclass(frozen=True)
class OperationOrderView:
    order_id: UUID
    order_code: str
    merchant_id: UUID
    merchant_name: str
    assignment_status: str
    vehicle_route_id: UUID | None
    vehicle_id: UUID | None
    driver_id: UUID | None
    execution_status: str
    risk_status: str
    pickup_eta: datetime | None
    delivery_eta: datetime | None


@dataclass(frozen=True)
class OperationVehicleView:
    vehicle_id: UUID
    vehicle_code: str
    vehicle_status: str
    current_location_id: UUID
    driver_id: UUID
    driver_code: str
    driver_status: str
    route_id: UUID
    route_status: str


@dataclass(frozen=True)
class OperationRouteView:
    route_id: UUID
    route_no: int
    vehicle_id: UUID
    driver_id: UUID
    status: str
    completed_stops: int
    total_stops: int
    current_stop_id: UUID | None
    current_stop_type: str | None
    next_stop_id: UUID | None
    next_stop_type: str | None
    next_stop_eta: datetime | None
    eta_deviation_seconds: int
    at_risk_orders: int


class OperationsQueryService:
    def __init__(self, session: Session) -> None:
        self.plans = PlanRepository(session)
        self.risk = RiskService(
            threshold_seconds=get_settings().at_risk_threshold_seconds
        )

    def dashboard(self, business_date: date) -> OperationsDashboardView:
        plan = self._current_plan(business_date)
        now = datetime.now(timezone.utc)
        orders = self._order_views(plan, now)
        routes = list(plan.routes)
        vehicles = [route.vehicle for route in routes]
        drivers = [route.driver for route in routes]
        merchants = {
            membership.order.merchant.id: membership.order.merchant
            for membership in plan.plan_orders
        }.values()
        open_incidents = [
            incident
            for incident in plan.incidents
            if incident.status is not IncidentStatus.RESOLVED
        ]
        return OperationsDashboardView(
            current_plan=CurrentPlanView(
                delivery_plan_id=plan.id,
                plan_code=plan.plan_code,
                version_no=plan.version_no,
            ),
            orders=CountView(
                total=len(orders),
                completed=sum(
                    item.execution_status == OrderExecutionStatus.COMPLETED
                    for item in orders
                ),
                in_progress=sum(
                    item.execution_status
                    not in (
                        OrderExecutionStatus.PLANNED,
                        OrderExecutionStatus.COMPLETED,
                    )
                    for item in orders
                ),
                at_risk=sum(
                    item.risk_status == OrderRiskStatus.AT_RISK
                    for item in orders
                ),
            ),
            vehicles=self._resource_counts(vehicles),
            drivers=self._resource_counts(drivers),
            merchants=CountView(
                total=len(merchants),
                ready=sum(
                    merchant.preparation_status
                    is MerchantPreparationStatus.READY
                    for merchant in merchants
                ),
                preparing=sum(
                    merchant.preparation_status
                    is MerchantPreparationStatus.PREPARING
                    for merchant in merchants
                ),
                delayed=sum(
                    merchant.preparation_status
                    is MerchantPreparationStatus.DELAYED
                    for merchant in merchants
                ),
            ),
            open_incidents=len(open_incidents),
            pending_recovery_reviews=sum(
                recovery.status is RecoveryPlanStatus.PENDING_REVIEW
                for incident in open_incidents
                for recovery in incident.recovery_plans
            ),
            calculated_at=now,
        )

    def list_orders(
        self,
        business_date: date,
        *,
        page: int,
        page_size: int,
        execution_status: OrderExecutionStatus | None = None,
        risk_status: OrderRiskStatus | None = None,
    ) -> tuple[list[OperationOrderView], int]:
        items = self._order_views(
            self._current_plan(business_date), datetime.now(timezone.utc)
        )
        if execution_status is not None:
            items = [
                item for item in items if item.execution_status == execution_status
            ]
        if risk_status is not None:
            items = [item for item in items if item.risk_status == risk_status]
        return self._page(items, page, page_size)

    def list_vehicles(
        self, business_date: date, *, page: int, page_size: int
    ) -> tuple[list[OperationVehicleView], int]:
        plan = self._current_plan(business_date)
        items = [
            OperationVehicleView(
                vehicle_id=route.vehicle.id,
                vehicle_code=route.vehicle.vehicle_code,
                vehicle_status=route.vehicle.status,
                current_location_id=route.vehicle.current_location_id,
                driver_id=route.driver.id,
                driver_code=route.driver.driver_code,
                driver_status=route.driver.status,
                route_id=route.id,
                route_status=route.status,
            )
            for route in sorted(plan.routes, key=lambda item: item.vehicle.vehicle_code)
        ]
        return self._page(items, page, page_size)

    def list_routes(
        self, business_date: date, *, page: int, page_size: int
    ) -> tuple[list[OperationRouteView], int]:
        plan = self._current_plan(business_date)
        now = datetime.now(timezone.utc)
        items = [
            self._route_view(route, now)
            for route in sorted(plan.routes, key=lambda item: item.route_no)
        ]
        return self._page(items, page, page_size)

    def _current_plan(self, business_date: date):
        plan = self.plans.get_current_plan_for_operations(business_date)
        if plan is None:
            raise NotFound(
                code="CURRENT_PLAN_NOT_FOUND",
                message=f"No current delivery plan for {business_date.isoformat()}",
            )
        return plan

    def _order_views(self, plan, now: datetime) -> list[OperationOrderView]:
        route_by_id = {route.id: route for route in plan.routes}
        result: list[OperationOrderView] = []
        for membership in sorted(
            plan.plan_orders, key=lambda item: item.order.order_code
        ):
            order = membership.order
            route = (
                route_by_id.get(membership.vehicle_route_id)
                if membership.assignment_status
                is PlanOrderAssignmentStatus.ASSIGNED
                else None
            )
            pickup_eta = None
            delivery_eta = None
            risk_status = order.risk_status
            if route is not None:
                delay = self.risk.route_delay_seconds(
                    stops=list(route.stops), current_time=now
                )
                for stop in route.stops:
                    if stop.order_id != order.id:
                        continue
                    eta = stop.planned_arrival_at + timedelta(seconds=delay)
                    if stop.stop_type is StopType.PICKUP:
                        pickup_eta = eta
                    elif stop.stop_type is StopType.DELIVERY:
                        delivery_eta = eta
                        if order.execution_status is OrderExecutionStatus.COMPLETED:
                            risk_status = OrderRiskStatus.NORMAL
                        else:
                            risk_status = self.risk.status_for_eta(
                                estimated_arrival_at=eta,
                                delivery_window_end_at=(
                                    stop.time_window_end_at
                                    or order.delivery_window_end_at
                                ),
                            )
            result.append(
                OperationOrderView(
                    order_id=order.id,
                    order_code=order.order_code,
                    merchant_id=order.merchant_id,
                    merchant_name=order.merchant.name,
                    assignment_status=membership.assignment_status,
                    vehicle_route_id=route.id if route else None,
                    vehicle_id=route.vehicle_id if route else None,
                    driver_id=route.driver_id if route else None,
                    execution_status=order.execution_status,
                    risk_status=risk_status,
                    pickup_eta=pickup_eta,
                    delivery_eta=delivery_eta,
                )
            )
        return result

    def _route_view(
        self, route: VehicleRoute, now: datetime
    ) -> OperationRouteView:
        stops = list(route.stops)
        current_stop = next(
            (
                stop
                for stop in stops
                if stop.status in (StopStatus.ARRIVED, StopStatus.IN_SERVICE)
            ),
            None,
        )
        next_stop = next(
            (stop for stop in stops if stop.status is StopStatus.PLANNED),
            None,
        )
        delay = self.risk.route_delay_seconds(stops=stops, current_time=now)
        at_risk_orders = {
            stop.order_id
            for stop in stops
            if stop.stop_type is StopType.DELIVERY
            and stop.status is not StopStatus.COMPLETED
            and self.risk.status_for_eta(
                estimated_arrival_at=stop.planned_arrival_at
                + timedelta(seconds=delay),
                delivery_window_end_at=(
                    stop.time_window_end_at
                    or stop.order.delivery_window_end_at
                ),
            )
            is OrderRiskStatus.AT_RISK
        }
        return OperationRouteView(
            route_id=route.id,
            route_no=route.route_no,
            vehicle_id=route.vehicle_id,
            driver_id=route.driver_id,
            status=route.status,
            completed_stops=sum(
                stop.status is StopStatus.COMPLETED for stop in stops
            ),
            total_stops=len(stops),
            current_stop_id=current_stop.id if current_stop else None,
            current_stop_type=current_stop.stop_type if current_stop else None,
            next_stop_id=next_stop.id if next_stop else None,
            next_stop_type=next_stop.stop_type if next_stop else None,
            next_stop_eta=(
                next_stop.planned_arrival_at + timedelta(seconds=delay)
                if next_stop
                else None
            ),
            eta_deviation_seconds=delay,
            at_risk_orders=len(at_risk_orders),
        )

    @staticmethod
    def _resource_counts(resources: list) -> CountView:
        return CountView(
            total=len(resources),
            available=sum(
                resource.status is ResourceStatus.AVAILABLE
                for resource in resources
            ),
            active=sum(
                resource.status is ResourceStatus.ACTIVE for resource in resources
            ),
            unavailable=sum(
                resource.status is ResourceStatus.UNAVAILABLE
                for resource in resources
            ),
        )

    @staticmethod
    def _page(items: list, page: int, page_size: int):
        total = len(items)
        offset = (page - 1) * page_size
        return items[offset : offset + page_size], total
