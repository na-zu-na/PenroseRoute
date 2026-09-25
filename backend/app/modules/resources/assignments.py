"""Vehicle–Driver pair lifecycle and deterministic overlap checks."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import BusinessError, Conflict, NotFound
from app.db.models import VehicleDriverAssignment
from app.db.models.fleet import AssignmentStatus, ResourceStatus
from app.db.repositories.fleet_repository import FleetRepository


class AssignmentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = FleetRepository(session)

    def list_assignments(
        self, *, page: int, page_size: int, business_date: date | None,
        status: AssignmentStatus | None, vehicle_id: UUID | None, driver_id: UUID | None,
    ) -> tuple[list[VehicleDriverAssignment], int]:
        window_start = window_end = None
        if business_date is not None:
            zone = ZoneInfo(get_settings().business_timezone)
            window_start = datetime.combine(business_date, time.min, zone)
            window_end = datetime.combine(business_date + timedelta(days=1), time.min, zone)
        filters = dict(
            window_start=window_start, window_end=window_end, status=status,
            vehicle_id=vehicle_id, driver_id=driver_id,
        )
        return (
            self.repository.list_assignments(page=page, page_size=page_size, **filters),
            self.repository.count_assignments(**filters),
        )

    def get_assignment(self, assignment_id: UUID) -> VehicleDriverAssignment:
        assignment = self.repository.get_assignment_by_id(assignment_id)
        if assignment is None:
            raise NotFound(code="ASSIGNMENT_NOT_FOUND", message="Assignment was not found")
        return assignment

    def create_assignment(
        self, *, business_date: date, vehicle_id: UUID, driver_id: UUID,
        assignment_start_at: datetime, assignment_end_at: datetime,
    ) -> VehicleDriverAssignment:
        zone = ZoneInfo(get_settings().business_timezone)
        if assignment_start_at.astimezone(zone).date() != business_date:
            raise BusinessError(
                code="VALIDATION_ERROR", message="Assignment start must belong to business_date",
            )
        if assignment_end_at <= assignment_start_at:
            raise BusinessError(code="VALIDATION_ERROR", message="Assignment time range is invalid")
        try:
            # Lock both resources before checking overlap so concurrent creators serialize.
            vehicle = self.repository.lock_vehicle_by_id(vehicle_id)
            if vehicle is None:
                raise NotFound(code="VEHICLE_NOT_FOUND", message="Vehicle was not found")
            driver = self.repository.lock_driver_by_id(driver_id)
            if driver is None:
                raise NotFound(code="DRIVER_NOT_FOUND", message="Driver was not found")
            if vehicle.status is not ResourceStatus.AVAILABLE:
                raise Conflict(code="VEHICLE_NOT_AVAILABLE", message="Vehicle is not available")
            if driver.status is not ResourceStatus.AVAILABLE:
                raise Conflict(code="DRIVER_NOT_AVAILABLE", message="Driver is not available")
            if self.repository.has_assignment_overlap(
                vehicle_id=vehicle_id, driver_id=driver_id,
                start=assignment_start_at, end=assignment_end_at,
            ):
                raise Conflict(
                    code="VEHICLE_DRIVER_ASSIGNMENT_CONFLICT",
                    message="Vehicle or driver already has an overlapping assignment",
                )
            assignment = VehicleDriverAssignment(
                vehicle=vehicle, driver=driver,
                assigned_from_at=assignment_start_at,
                assigned_until_at=assignment_end_at,
                status=AssignmentStatus.PLANNED,
            )
            self.repository.add_assignment(assignment)
            self.repository.flush()
            self.session.commit()
            return assignment
        except IntegrityError as error:
            self.session.rollback()
            raise Conflict(
                code="VEHICLE_DRIVER_ASSIGNMENT_CONFLICT",
                message="Vehicle or driver already has an overlapping assignment",
            ) from error

    def activate(self, assignment_id: UUID) -> VehicleDriverAssignment:
        assignment = self._lock_assignment(assignment_id)
        if assignment.status is not AssignmentStatus.PLANNED:
            raise Conflict(code="INVALID_ASSIGNMENT_STATE_TRANSITION", message="Only planned assignments can be activated")
        now = datetime.now(timezone.utc)
        if now < assignment.assigned_from_at or (
            assignment.assigned_until_at is not None and now >= assignment.assigned_until_at
        ):
            raise Conflict(code="INVALID_ASSIGNMENT_STATE_TRANSITION", message="Assignment is outside its active time range")
        vehicle = self.repository.lock_vehicle_by_id(assignment.vehicle_id)
        driver = self.repository.lock_driver_by_id(assignment.driver_id)
        if vehicle.status is not ResourceStatus.AVAILABLE:
            raise Conflict(code="VEHICLE_NOT_AVAILABLE", message="Vehicle is not available")
        if driver.status is not ResourceStatus.AVAILABLE:
            raise Conflict(code="DRIVER_NOT_AVAILABLE", message="Driver is not available")
        assignment.status = AssignmentStatus.ACTIVE
        assignment.activated_at = now
        assignment.updated_at = now
        self.repository.flush()
        self.session.commit()
        return self.get_assignment(assignment_id)

    def end(self, assignment_id: UUID) -> VehicleDriverAssignment:
        assignment = self._lock_assignment(assignment_id)
        if assignment.status is not AssignmentStatus.ACTIVE:
            raise Conflict(code="INVALID_ASSIGNMENT_STATE_TRANSITION", message="Only active assignments can be ended")
        now = datetime.now(timezone.utc)
        assignment.status = AssignmentStatus.ENDED
        assignment.ended_at = now
        assignment.updated_at = now
        self.repository.flush()
        self.session.commit()
        return self.get_assignment(assignment_id)

    def cancel(self, assignment_id: UUID) -> VehicleDriverAssignment:
        assignment = self._lock_assignment(assignment_id)
        if assignment.status is not AssignmentStatus.PLANNED:
            raise Conflict(code="INVALID_ASSIGNMENT_STATE_TRANSITION", message="Only planned assignments can be cancelled")
        assignment.status = AssignmentStatus.CANCELLED
        assignment.updated_at = datetime.now(timezone.utc)
        self.repository.flush()
        self.session.commit()
        return self.get_assignment(assignment_id)

    def _lock_assignment(self, assignment_id: UUID) -> VehicleDriverAssignment:
        assignment = self.repository.lock_assignment_by_id(assignment_id)
        if assignment is None:
            raise NotFound(code="ASSIGNMENT_NOT_FOUND", message="Assignment was not found")
        return assignment
