from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Driver, Vehicle, VehicleDriverAssignment
from app.db.models.fleet import AssignmentStatus
from app.db.models.planning import RouteStatus, VehicleRoute


class FleetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_vehicle_by_id(self, vehicle_id: UUID) -> Vehicle | None:
        statement = (
            select(Vehicle)
            .where(Vehicle.id == vehicle_id)
            .options(joinedload(Vehicle.current_location))
        )
        return self.session.scalar(statement)

    def lock_vehicle_by_id(self, vehicle_id: UUID) -> Vehicle | None:
        statement = (
            select(Vehicle)
            .where(Vehicle.id == vehicle_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def has_active_route_for_vehicle(self, vehicle_id: UUID) -> bool:
        statement = select(
            exists().where(
                VehicleRoute.vehicle_id == vehicle_id,
                VehicleRoute.status == RouteStatus.ACTIVE,
            )
        )
        return bool(self.session.scalar(statement))

    def get_driver_by_id(self, driver_id: UUID) -> Driver | None:
        return self.session.get(Driver, driver_id)

    def lock_driver_by_id(self, driver_id: UUID) -> Driver | None:
        statement = select(Driver).where(Driver.id == driver_id).with_for_update()
        return self.session.scalar(statement)

    def lock_assignment_by_id(
        self, assignment_id: UUID
    ) -> VehicleDriverAssignment | None:
        statement = (
            select(VehicleDriverAssignment)
            .where(VehicleDriverAssignment.id == assignment_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def list_vehicles(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> list[Vehicle]:
        statement = select(Vehicle).options(joinedload(Vehicle.current_location))
        if status is not None:
            statement = statement.where(Vehicle.status == status)
        statement = statement.order_by(Vehicle.vehicle_code).offset(
            (page - 1) * page_size
        ).limit(page_size)
        return list(self.session.scalars(statement))

    def count_vehicles(self, *, status: str | None = None) -> int:
        statement = select(func.count()).select_from(Vehicle)
        if status is not None:
            statement = statement.where(Vehicle.status == status)
        return self.session.scalar(statement) or 0

    def list_drivers(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
    ) -> list[Driver]:
        statement = select(Driver)
        if status is not None:
            statement = statement.where(Driver.status == status)
        statement = statement.order_by(Driver.driver_code).offset(
            (page - 1) * page_size
        ).limit(page_size)
        return list(self.session.scalars(statement))

    def count_drivers(self, *, status: str | None = None) -> int:
        statement = select(func.count()).select_from(Driver)
        if status is not None:
            statement = statement.where(Driver.status == status)
        return self.session.scalar(statement) or 0

    def get_active_vehicle_driver_pairs(
        self, operational_at: datetime
    ) -> list[VehicleDriverAssignment]:
        statement = (
            select(VehicleDriverAssignment)
            .join(VehicleDriverAssignment.vehicle)
            .where(
                VehicleDriverAssignment.status.in_(
                    (AssignmentStatus.PLANNED, AssignmentStatus.ACTIVE)
                ),
                VehicleDriverAssignment.assigned_from_at <= operational_at,
                or_(
                    VehicleDriverAssignment.assigned_until_at.is_(None),
                    VehicleDriverAssignment.assigned_until_at > operational_at,
                ),
            )
            .options(
                joinedload(VehicleDriverAssignment.vehicle).joinedload(
                    Vehicle.current_location
                ),
                joinedload(VehicleDriverAssignment.driver),
            )
            .order_by(Vehicle.vehicle_code)
        )
        return list(self.session.scalars(statement))

    def get_vehicle_driver_pairs_for_window(
        self,
        operational_from: datetime,
        operational_until: datetime,
    ) -> list[VehicleDriverAssignment]:
        statement = (
            select(VehicleDriverAssignment)
            .join(VehicleDriverAssignment.vehicle)
            .where(
                VehicleDriverAssignment.status.in_(
                    (AssignmentStatus.PLANNED, AssignmentStatus.ACTIVE)
                ),
                VehicleDriverAssignment.assigned_from_at <= operational_until,
                or_(
                    VehicleDriverAssignment.assigned_until_at.is_(None),
                    VehicleDriverAssignment.assigned_until_at > operational_from,
                ),
            )
            .options(
                joinedload(VehicleDriverAssignment.vehicle).joinedload(
                    Vehicle.current_location
                ),
                joinedload(VehicleDriverAssignment.driver),
            )
            .order_by(Vehicle.vehicle_code)
        )
        return list(self.session.scalars(statement))

    def add_vehicle(self, vehicle: Vehicle) -> None:
        self.session.add(vehicle)

    def add_driver(self, driver: Driver) -> None:
        self.session.add(driver)

    def add_assignment(self, assignment: VehicleDriverAssignment) -> None:
        self.session.add(assignment)

    def flush(self) -> None:
        self.session.flush()
