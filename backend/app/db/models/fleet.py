from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.planning import VehicleRoute
    from app.db.models.recovery import Incident
    from app.db.models.resources import Location


def _enum(enum: type[StrEnum], *, length: int, name: str) -> Enum:
    return Enum(enum, name=name, native_enum=False, create_constraint=False, validate_strings=True, length=length)


class ResourceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ACTIVE = "ACTIVE"
    UNAVAILABLE = "UNAVAILABLE"


class AssignmentStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint("capacity_load_units > 0", name="ck_vehicles_capacity"),
        CheckConstraint("btrim(vehicle_code) <> ''", name="ck_vehicles_code"),
        CheckConstraint("btrim(name) <> ''", name="ck_vehicles_name"),
        CheckConstraint("status IN ('AVAILABLE','ACTIVE','UNAVAILABLE')", name="ck_vehicles_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    vehicle_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    capacity_load_units: Mapped[int] = mapped_column(Integer)
    status: Mapped[ResourceStatus] = mapped_column(
        _enum(ResourceStatus, length=16, name="vehicle_status"), server_default=text("'AVAILABLE'")
    )
    current_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    current_location_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    current_location: Mapped[Location] = relationship(back_populates="vehicles")
    assignments: Mapped[list[VehicleDriverAssignment]] = relationship(back_populates="vehicle")
    routes: Mapped[list[VehicleRoute]] = relationship(
        back_populates="vehicle", overlaps="routes,vehicle_driver_assignment"
    )
    incidents: Mapped[list[Incident]] = relationship(back_populates="vehicle")


class Driver(Base):
    __tablename__ = "drivers"
    __table_args__ = (
        CheckConstraint("btrim(driver_code) <> ''", name="ck_drivers_code"),
        CheckConstraint("btrim(name) <> ''", name="ck_drivers_name"),
        CheckConstraint("status IN ('AVAILABLE','ACTIVE','UNAVAILABLE')", name="ck_drivers_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    driver_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[ResourceStatus] = mapped_column(
        _enum(ResourceStatus, length=16, name="driver_status"), server_default=text("'AVAILABLE'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    assignments: Mapped[list[VehicleDriverAssignment]] = relationship(back_populates="driver")
    routes: Mapped[list[VehicleRoute]] = relationship(
        back_populates="driver", overlaps="routes,vehicle_driver_assignment"
    )


class VehicleDriverAssignment(Base):
    __tablename__ = "vehicle_driver_assignments"
    __table_args__ = (
        UniqueConstraint("id", "vehicle_id", "driver_id", name="uq_assignments_identity"),
        CheckConstraint("assigned_until_at IS NULL OR assigned_until_at > assigned_from_at", name="ck_assignments_window"),
        CheckConstraint("status IN ('PLANNED','ACTIVE','ENDED','CANCELLED')", name="ck_assignments_status"),
        CheckConstraint("status <> 'ACTIVE' OR activated_at IS NOT NULL", name="ck_assignments_active_time"),
        CheckConstraint(
            "status <> 'ENDED' OR (ended_at IS NOT NULL AND (activated_at IS NULL OR ended_at >= activated_at))",
            name="ck_assignments_ended_time",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    vehicle_id: Mapped[UUID] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id", ondelete="RESTRICT"))
    assigned_from_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    assigned_until_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[AssignmentStatus] = mapped_column(
        _enum(AssignmentStatus, length=16, name="assignment_status"), server_default=text("'PLANNED'")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    vehicle: Mapped[Vehicle] = relationship(back_populates="assignments")
    driver: Mapped[Driver] = relationship(back_populates="assignments")
    routes: Mapped[list[VehicleRoute]] = relationship(
        back_populates="vehicle_driver_assignment", overlaps="driver,routes,vehicle"
    )
