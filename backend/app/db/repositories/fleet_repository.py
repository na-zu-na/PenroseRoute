from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Driver, Vehicle, VehicleDriverAssignment
from app.db.models.fleet import AssignmentStatus


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

    def get_driver_by_id(self, driver_id: UUID) -> Driver | None:
        return self.session.get(Driver, driver_id)

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

    def add_vehicle(self, vehicle: Vehicle) -> None:
        self.session.add(vehicle)

    def add_driver(self, driver: Driver) -> None:
        self.session.add(driver)

    def add_assignment(self, assignment: VehicleDriverAssignment) -> None:
        self.session.add(assignment)

    def flush(self) -> None:
        self.session.flush()
