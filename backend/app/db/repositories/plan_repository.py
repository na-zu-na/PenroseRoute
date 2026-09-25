from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db.models import (
    DeliveryPlan,
    DeliveryPlanOrder,
    Incident,
    Order,
    RouteStop,
    Vehicle,
    VehicleRoute,
)
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
    RouteStatus,
)


class PlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_current_plan(self, business_date: date) -> DeliveryPlan | None:
        statement = select(DeliveryPlan).where(
            DeliveryPlan.business_date == business_date,
            DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
        )
        return self.session.scalar(statement)

    def get_current_plan_for_operations(
        self, business_date: date
    ) -> DeliveryPlan | None:
        statement = (
            select(DeliveryPlan)
            .where(
                DeliveryPlan.business_date == business_date,
                DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
            )
            .options(
                selectinload(DeliveryPlan.plan_orders)
                .joinedload(DeliveryPlanOrder.order)
                .joinedload(Order.merchant),
                selectinload(DeliveryPlan.routes)
                .joinedload(VehicleRoute.vehicle)
                .joinedload(Vehicle.current_location),
                selectinload(DeliveryPlan.routes).joinedload(
                    VehicleRoute.driver
                ),
                selectinload(DeliveryPlan.routes).joinedload(
                    VehicleRoute.vehicle_driver_assignment
                ),
                selectinload(DeliveryPlan.routes)
                .selectinload(VehicleRoute.stops)
                .joinedload(RouteStop.order),
                selectinload(DeliveryPlan.incidents).selectinload(
                    Incident.recovery_plans
                ),
            )
        )
        return self.session.scalar(statement)

    def list_plans(
        self,
        *,
        page: int,
        page_size: int,
        business_date: date | None = None,
        status: DeliveryPlanStatus | None = None,
        plan_group_id: UUID | None = None,
    ) -> list[DeliveryPlan]:
        statement = select(DeliveryPlan)
        if business_date is not None:
            statement = statement.where(DeliveryPlan.business_date == business_date)
        if status is not None:
            statement = statement.where(DeliveryPlan.status == status)
        if plan_group_id is not None:
            statement = statement.where(DeliveryPlan.plan_group_id == plan_group_id)
        statement = statement.order_by(
            DeliveryPlan.business_date.desc(),
            DeliveryPlan.version_no.desc(),
        ).offset((page - 1) * page_size).limit(page_size)
        return list(self.session.scalars(statement))

    def count_plans(
        self,
        *,
        business_date: date | None = None,
        status: DeliveryPlanStatus | None = None,
        plan_group_id: UUID | None = None,
    ) -> int:
        statement = select(func.count()).select_from(DeliveryPlan)
        if business_date is not None:
            statement = statement.where(DeliveryPlan.business_date == business_date)
        if status is not None:
            statement = statement.where(DeliveryPlan.status == status)
        if plan_group_id is not None:
            statement = statement.where(DeliveryPlan.plan_group_id == plan_group_id)
        return int(self.session.scalar(statement) or 0)

    def get_plan_by_id(self, plan_id: UUID) -> DeliveryPlan | None:
        return self.session.get(DeliveryPlan, plan_id)

    def list_plan_orders(
        self,
        plan_id: UUID,
        *,
        page: int,
        page_size: int,
        assignment_status: PlanOrderAssignmentStatus | None = None,
    ) -> list[DeliveryPlanOrder]:
        statement = (
            select(DeliveryPlanOrder)
            .where(DeliveryPlanOrder.delivery_plan_id == plan_id)
            .options(selectinload(DeliveryPlanOrder.order))
            .order_by(DeliveryPlanOrder.created_at, DeliveryPlanOrder.id)
        )
        if assignment_status is not None:
            statement = statement.where(
                DeliveryPlanOrder.assignment_status == assignment_status
            )
        statement = statement.offset((page - 1) * page_size).limit(page_size)
        return list(self.session.scalars(statement))

    def count_plan_orders(
        self,
        plan_id: UUID,
        *,
        assignment_status: PlanOrderAssignmentStatus | None = None,
    ) -> int:
        statement = select(func.count()).select_from(DeliveryPlanOrder).where(
            DeliveryPlanOrder.delivery_plan_id == plan_id
        )
        if assignment_status is not None:
            statement = statement.where(
                DeliveryPlanOrder.assignment_status == assignment_status
            )
        return int(self.session.scalar(statement) or 0)

    def list_plan_routes(self, plan_id: UUID) -> list[VehicleRoute]:
        statement = (
            select(VehicleRoute)
            .where(VehicleRoute.delivery_plan_id == plan_id)
            .order_by(VehicleRoute.route_no)
        )
        return list(self.session.scalars(statement))

    def get_route_by_id(self, route_id: UUID) -> VehicleRoute | None:
        return self.session.get(VehicleRoute, route_id)

    def list_route_stops(self, route_id: UUID) -> list[RouteStop]:
        statement = (
            select(RouteStop)
            .where(RouteStop.vehicle_route_id == route_id)
            .order_by(RouteStop.sequence_no)
        )
        return list(self.session.scalars(statement))

    def get_route_stop_by_id(self, stop_id: UUID) -> RouteStop | None:
        return self.session.get(RouteStop, stop_id)

    def lock_route_stop_by_id(self, stop_id: UUID) -> RouteStop | None:
        statement = select(RouteStop).where(RouteStop.id == stop_id).with_for_update()
        return self.session.scalar(statement)

    def lock_route_by_id(self, route_id: UUID) -> VehicleRoute | None:
        statement = (
            select(VehicleRoute)
            .where(VehicleRoute.id == route_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def lock_active_route_for_plan_vehicle(
        self, delivery_plan_id: UUID, vehicle_id: UUID
    ) -> VehicleRoute | None:
        statement = (
            select(VehicleRoute)
            .where(
                VehicleRoute.delivery_plan_id == delivery_plan_id,
                VehicleRoute.vehicle_id == vehicle_id,
                VehicleRoute.status == RouteStatus.ACTIVE,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def lock_plan_by_id(self, plan_id: UUID) -> DeliveryPlan | None:
        statement = (
            select(DeliveryPlan)
            .where(DeliveryPlan.id == plan_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def list_route_stops_with_orders(self, route_id: UUID) -> list[RouteStop]:
        statement = (
            select(RouteStop)
            .where(RouteStop.vehicle_route_id == route_id)
            .options(joinedload(RouteStop.order))
            .order_by(RouteStop.sequence_no)
        )
        return list(self.session.scalars(statement))

    def list_route_plan_orders(
        self, route_id: UUID
    ) -> list[DeliveryPlanOrder]:
        statement = (
            select(DeliveryPlanOrder)
            .join(DeliveryPlanOrder.order)
            .where(
                DeliveryPlanOrder.vehicle_route_id == route_id,
                DeliveryPlanOrder.assignment_status
                == PlanOrderAssignmentStatus.ASSIGNED,
            )
            .options(joinedload(DeliveryPlanOrder.order))
            .order_by(Order.order_code)
        )
        return list(self.session.scalars(statement))

    def get_latest_plan_for_business_date(
        self, business_date: date
    ) -> DeliveryPlan | None:
        statement = (
            select(DeliveryPlan)
            .where(DeliveryPlan.business_date == business_date)
            .order_by(DeliveryPlan.version_no.desc(), DeliveryPlan.created_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def lock_business_date_for_planning(self, business_date: date) -> None:
        self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(f"planning:{business_date.isoformat()}")
                )
            )
        )

    def get_plan_with_routes(self, plan_id: UUID) -> DeliveryPlan | None:
        statement = (
            select(DeliveryPlan)
            .where(DeliveryPlan.id == plan_id)
            .options(
                selectinload(DeliveryPlan.plan_orders),
                selectinload(DeliveryPlan.routes).selectinload(VehicleRoute.stops),
            )
        )
        return self.session.scalar(statement)

    def get_route_with_stops(self, route_id: UUID) -> VehicleRoute | None:
        statement = (
            select(VehicleRoute)
            .where(VehicleRoute.id == route_id)
            .options(selectinload(VehicleRoute.stops))
        )
        return self.session.scalar(statement)

    def add_delivery_plan(self, delivery_plan: DeliveryPlan) -> None:
        self.session.add(delivery_plan)

    def add_vehicle_route(self, route: VehicleRoute) -> None:
        self.session.add(route)

    def add_delivery_plan_order(self, plan_order: DeliveryPlanOrder) -> None:
        self.session.add(plan_order)

    def add_route_stop(self, stop: RouteStop) -> None:
        self.session.add(stop)

    def flush(self) -> None:
        self.session.flush()
