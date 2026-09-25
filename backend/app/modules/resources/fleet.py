from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.db.models import Driver, Vehicle
from app.db.models.fleet import ResourceStatus
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.resources.locations import resolve_location


class FleetService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = FleetRepository(session)
        self.resource_repository = ResourceRepository(session)

    def list_vehicles(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[Vehicle], int]:
        return (
            self.repository.list_vehicles(
                page=page,
                page_size=page_size,
                status=status,
            ),
            self.repository.count_vehicles(status=status),
        )

    def get_vehicle(self, vehicle_id: UUID) -> Vehicle:
        vehicle = self.repository.get_vehicle_by_id(vehicle_id)
        if vehicle is None:
            raise NotFound(
                code="VEHICLE_NOT_FOUND",
                message="Vehicle was not found",
            )
        return vehicle

    def create_vehicle(
        self,
        *,
        vehicle_code: str,
        name: str,
        capacity_load_units: int,
        location_code: str,
        address_text: str | None,
        latitude: Decimal,
        longitude: Decimal,
        current_location_recorded_at: datetime,
    ) -> Vehicle:
        try:
            location = resolve_location(
                self.resource_repository,
                location_code=location_code,
                display_name=name,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            vehicle = Vehicle(
                vehicle_code=vehicle_code,
                name=name,
                capacity_load_units=capacity_load_units,
                status=ResourceStatus.AVAILABLE,
                current_location=location,
                current_location_recorded_at=current_location_recorded_at,
            )
            self.repository.add_vehicle(vehicle)
            self.repository.flush()
            self.session.commit()
            return vehicle
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Vehicle code or location code already exists",
            ) from error

    def replace_vehicle(
        self,
        vehicle_id: UUID,
        *,
        name: str,
        capacity_load_units: int,
    ) -> Vehicle:
        vehicle = self.get_vehicle(vehicle_id)
        vehicle.name = name
        vehicle.capacity_load_units = capacity_load_units
        vehicle.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return vehicle

    def update_vehicle_status(
        self,
        vehicle_id: UUID,
        *,
        status: str,
        business_date: date | None,
    ) -> Vehicle:
        del business_date
        vehicle = self.repository.lock_vehicle_by_id(vehicle_id)
        if vehicle is None:
            raise NotFound(
                code="VEHICLE_NOT_FOUND",
                message="Vehicle was not found",
            )
        target = ResourceStatus(status)
        if vehicle.status == target:
            return vehicle
        if (
            vehicle.status == ResourceStatus.ACTIVE
            and target == ResourceStatus.UNAVAILABLE
        ):
            raise Conflict(
                code="VEHICLE_INCIDENT_WORKFLOW_REQUIRED",
                message="ACTIVE to UNAVAILABLE must be handled by the incident workflow",
            )
        allowed = {
            (ResourceStatus.AVAILABLE, ResourceStatus.UNAVAILABLE),
            (ResourceStatus.UNAVAILABLE, ResourceStatus.AVAILABLE),
        }
        if (vehicle.status, target) not in allowed:
            raise Conflict(
                code="INVALID_VEHICLE_STATUS_TRANSITION",
                message="Vehicle status transition requires a route workflow",
            )
        if (
            vehicle.status == ResourceStatus.AVAILABLE
            and target == ResourceStatus.UNAVAILABLE
            and self.repository.has_active_route_for_vehicle(vehicle.id)
        ):
            raise Conflict(
                code="VEHICLE_NOT_AVAILABLE",
                message="Vehicle with an active route cannot use a resource-only status update",
            )
        vehicle.status = target
        vehicle.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return vehicle

    def update_vehicle_location(
        self,
        vehicle_id: UUID,
        *,
        location_code: str,
        address_text: str | None,
        latitude: Decimal,
        longitude: Decimal,
        recorded_at: datetime,
    ) -> Vehicle:
        vehicle = self.get_vehicle(vehicle_id)
        try:
            vehicle.current_location = resolve_location(
                self.resource_repository,
                location_code=location_code,
                display_name=vehicle.name,
                address_text=address_text,
                latitude=latitude,
                longitude=longitude,
            )
            vehicle.current_location_recorded_at = recorded_at
            vehicle.updated_at = datetime.now(timezone.utc)
            self.repository.flush()
            self.session.commit()
            return vehicle
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Vehicle location conflicts with an existing location",
            ) from error

    def list_drivers(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None,
    ) -> tuple[list[Driver], int]:
        return (
            self.repository.list_drivers(
                page=page,
                page_size=page_size,
                status=status,
            ),
            self.repository.count_drivers(status=status),
        )

    def get_driver(self, driver_id: UUID) -> Driver:
        driver = self.repository.get_driver_by_id(driver_id)
        if driver is None:
            raise NotFound(
                code="DRIVER_NOT_FOUND",
                message="Driver was not found",
            )
        return driver

    def create_driver(self, *, driver_code: str, name: str) -> Driver:
        try:
            driver = Driver(
                driver_code=driver_code,
                name=name,
                status=ResourceStatus.AVAILABLE,
            )
            self.repository.add_driver(driver)
            self.repository.flush()
            self.session.commit()
            return driver
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="RESOURCE_CONFLICT",
                message="Driver code already exists",
            ) from error

    def replace_driver(self, driver_id: UUID, *, name: str) -> Driver:
        driver = self.get_driver(driver_id)
        driver.name = name
        driver.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return driver

    def update_driver_status(
        self,
        driver_id: UUID,
        *,
        status: str,
    ) -> tuple[Driver, bool]:
        driver = self.get_driver(driver_id)
        target = ResourceStatus(status)
        if driver.status == target:
            return driver, False
        if (
            driver.status == ResourceStatus.UNAVAILABLE
            and target == ResourceStatus.ACTIVE
        ):
            raise Conflict(
                code="DRIVER_NOT_AVAILABLE",
                message="An unavailable driver must be restored before activation",
            )
        manual_intervention_required = (
            driver.status == ResourceStatus.ACTIVE
            and target == ResourceStatus.UNAVAILABLE
        )
        driver.status = target
        driver.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return driver, manual_intervention_required
