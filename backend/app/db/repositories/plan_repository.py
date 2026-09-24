from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import DeliveryPlan, RouteStop, VehicleRoute
from app.db.models.planning import DeliveryPlanStatus


class PlanRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_current_plan(self, business_date: date) -> DeliveryPlan | None:
        statement = select(DeliveryPlan).where(
            DeliveryPlan.business_date == business_date,
            DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
        )
        return self.session.scalar(statement)

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

    def add_route_stop(self, stop: RouteStop) -> None:
        self.session.add(stop)

    def flush(self) -> None:
        self.session.flush()
