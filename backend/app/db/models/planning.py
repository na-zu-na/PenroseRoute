from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.fleet import Driver, Vehicle, VehicleDriverAssignment
    from app.db.models.recovery import Incident, IncidentAffectedOrder, RecoveryPlan
    from app.db.models.resources import Location, Order


def _enum(enum: type[StrEnum], *, length: int, name: str) -> Enum:
    return Enum(enum, name=name, native_enum=False, create_constraint=False, validate_strings=True, length=length)


class DeliveryPlanStatus(StrEnum):
    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    VALID = "VALID"
    INVALID = "INVALID"


class PlanOrderAssignmentStatus(StrEnum):
    ASSIGNED = "ASSIGNED"
    UNASSIGNED = "UNASSIGNED"


class RouteStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class StopType(StrEnum):
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    HANDOVER = "HANDOVER"


class StopStatus(StrEnum):
    PLANNED = "PLANNED"
    ARRIVED = "ARRIVED"
    IN_SERVICE = "IN_SERVICE"
    COMPLETED = "COMPLETED"


class DeliveryPlan(Base):
    __tablename__ = "delivery_plans"
    __table_args__ = (
        UniqueConstraint("plan_group_id", "version_no", name="uq_delivery_plans_group_version"),
        CheckConstraint("version_no > 0", name="ck_delivery_plans_version"),
        CheckConstraint(
            "((version_no = 1 AND parent_plan_id IS NULL) OR (version_no > 1 AND parent_plan_id IS NOT NULL))",
            name="ck_delivery_plans_parent",
        ),
        CheckConstraint("solver_engine = 'OR_TOOLS'", name="ck_delivery_plans_solver"),
        CheckConstraint(
            "status IN ('DRAFT','CANDIDATE','CURRENT','SUPERSEDED','CANCELLED')",
            name="ck_delivery_plans_status",
        ),
        CheckConstraint("validation_status IN ('PENDING','VALID','INVALID')", name="ck_delivery_plans_validation"),
        CheckConstraint(
            "vehicle_count >= 0 AND assigned_order_count >= 0 AND unassigned_order_count >= 0",
            name="ck_delivery_plans_counts",
        ),
        CheckConstraint(
            "(total_distance_meters IS NULL OR total_distance_meters >= 0) AND "
            "(total_duration_seconds IS NULL OR total_duration_seconds >= 0)",
            name="ck_delivery_plans_metrics",
        ),
        CheckConstraint("status <> 'CURRENT' OR (validation_status = 'VALID' AND activated_at IS NOT NULL)", name="ck_delivery_plans_current"),
        CheckConstraint("status <> 'SUPERSEDED' OR superseded_at IS NOT NULL", name="ck_delivery_plans_superseded"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    plan_code: Mapped[str] = mapped_column(String(64), unique=True)
    plan_group_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    business_date: Mapped[date] = mapped_column(Date)
    version_no: Mapped[int] = mapped_column(Integer)
    parent_plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("delivery_plans.id", ondelete="RESTRICT"))
    status: Mapped[DeliveryPlanStatus] = mapped_column(
        _enum(DeliveryPlanStatus, length=16, name="delivery_plan_status"), server_default=text("'DRAFT'")
    )
    solver_engine: Mapped[str] = mapped_column(String(32), server_default=text("'OR_TOOLS'"))
    validation_status: Mapped[ValidationStatus] = mapped_column(
        _enum(ValidationStatus, length=16, name="delivery_plan_validation_status"), server_default=text("'PENDING'")
    )
    total_distance_meters: Mapped[int | None] = mapped_column(BigInteger)
    total_duration_seconds: Mapped[int | None] = mapped_column(BigInteger)
    vehicle_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    assigned_order_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    unassigned_order_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    validation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    parent_plan: Mapped[DeliveryPlan | None] = relationship(remote_side=[id], back_populates="child_plans")
    child_plans: Mapped[list[DeliveryPlan]] = relationship(back_populates="parent_plan")
    plan_orders: Mapped[list[DeliveryPlanOrder]] = relationship(
        back_populates="delivery_plan", overlaps="plan_orders,vehicle_route"
    )
    routes: Mapped[list[VehicleRoute]] = relationship(back_populates="delivery_plan", order_by="VehicleRoute.route_no")
    incidents: Mapped[list[Incident]] = relationship(back_populates="delivery_plan")
    base_recovery_plans: Mapped[list[RecoveryPlan]] = relationship(
        back_populates="base_delivery_plan", foreign_keys="RecoveryPlan.base_delivery_plan_id"
    )
    candidate_recovery_plan: Mapped[RecoveryPlan | None] = relationship(
        back_populates="candidate_delivery_plan", foreign_keys="RecoveryPlan.candidate_delivery_plan_id"
    )


class VehicleRoute(Base):
    __tablename__ = "vehicle_routes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["vehicle_driver_assignment_id", "vehicle_id", "driver_id"],
            [
                "vehicle_driver_assignments.id",
                "vehicle_driver_assignments.vehicle_id",
                "vehicle_driver_assignments.driver_id",
            ],
            name="fk_vehicle_routes_assignment",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "delivery_plan_id", name="uq_vehicle_routes_plan_identity"),
        UniqueConstraint("delivery_plan_id", "route_no", name="uq_vehicle_routes_plan_route_no"),
        UniqueConstraint("delivery_plan_id", "vehicle_id", name="uq_vehicle_routes_plan_vehicle"),
        UniqueConstraint("delivery_plan_id", "vehicle_driver_assignment_id", name="uq_vehicle_routes_plan_assignment"),
        CheckConstraint("route_no > 0", name="ck_vehicle_routes_route_no"),
        CheckConstraint("planned_end_at >= planned_start_at", name="ck_vehicle_routes_planned_window"),
        CheckConstraint(
            "actual_end_at IS NULL OR (actual_start_at IS NOT NULL AND actual_end_at >= actual_start_at)",
            name="ck_vehicle_routes_actual_window",
        ),
        CheckConstraint("distance_meters >= 0 AND duration_seconds >= 0", name="ck_vehicle_routes_metrics"),
        CheckConstraint("vehicle_capacity_load_units_snapshot > 0", name="ck_vehicle_routes_capacity"),
        CheckConstraint("status IN ('PLANNED','ACTIVE','COMPLETED','CANCELLED')", name="ck_vehicle_routes_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    delivery_plan_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_plans.id", ondelete="CASCADE"))
    route_no: Mapped[int] = mapped_column(Integer)
    vehicle_id: Mapped[UUID] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    driver_id: Mapped[UUID] = mapped_column(ForeignKey("drivers.id", ondelete="RESTRICT"))
    vehicle_driver_assignment_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    start_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    end_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    status: Mapped[RouteStatus] = mapped_column(
        _enum(RouteStatus, length=16, name="vehicle_route_status"), server_default=text("'PLANNED'")
    )
    planned_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    planned_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_meters: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    duration_seconds: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    vehicle_capacity_load_units_snapshot: Mapped[int] = mapped_column(Integer)
    route_geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    route_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    delivery_plan: Mapped[DeliveryPlan] = relationship(back_populates="routes")
    vehicle: Mapped[Vehicle] = relationship(
        back_populates="routes", foreign_keys=[vehicle_id], overlaps="routes,vehicle_driver_assignment"
    )
    driver: Mapped[Driver] = relationship(
        back_populates="routes", foreign_keys=[driver_id], overlaps="routes,vehicle_driver_assignment"
    )
    vehicle_driver_assignment: Mapped[VehicleDriverAssignment] = relationship(
        back_populates="routes", overlaps="driver,routes,vehicle"
    )
    start_location: Mapped[Location] = relationship(foreign_keys=[start_location_id])
    end_location: Mapped[Location] = relationship(foreign_keys=[end_location_id])
    plan_orders: Mapped[list[DeliveryPlanOrder]] = relationship(
        back_populates="vehicle_route", overlaps="delivery_plan,plan_orders"
    )
    stops: Mapped[list[RouteStop]] = relationship(back_populates="vehicle_route", order_by="RouteStop.sequence_no")
    incidents: Mapped[list[Incident]] = relationship(back_populates="vehicle_route")
    incident_impacts: Mapped[list[IncidentAffectedOrder]] = relationship(back_populates="original_vehicle_route")


class DeliveryPlanOrder(Base):
    __tablename__ = "delivery_plan_orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["vehicle_route_id", "delivery_plan_id"],
            ["vehicle_routes.id", "vehicle_routes.delivery_plan_id"],
            name="fk_delivery_plan_orders_route",
            ondelete="CASCADE",
        ),
        UniqueConstraint("delivery_plan_id", "order_id", name="uq_delivery_plan_orders_plan_order"),
        CheckConstraint(
            "(assignment_status = 'ASSIGNED' AND vehicle_route_id IS NOT NULL AND unassigned_reason_code IS NULL "
            "AND unassigned_reason_detail IS NULL) OR (assignment_status = 'UNASSIGNED' AND vehicle_route_id IS NULL "
            "AND unassigned_reason_code IS NOT NULL)",
            name="ck_delivery_plan_orders_assignment",
        ),
        CheckConstraint(
            "unassigned_reason_code IS NULL OR unassigned_reason_code IN "
            "('CAPACITY_INFEASIBLE','TIME_WINDOW_INFEASIBLE','RESOURCE_UNAVAILABLE','NO_FEASIBLE_ROUTE','INVALID_INPUT','OTHER')",
            name="ck_delivery_plan_orders_reason",
        ),
        CheckConstraint(
            "unassigned_reason_code <> 'OTHER' OR btrim(coalesce(unassigned_reason_detail, '')) <> ''",
            name="ck_delivery_plan_orders_other_reason",
        ),
        CheckConstraint("assignment_status IN ('ASSIGNED','UNASSIGNED')", name="ck_delivery_plan_orders_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    delivery_plan_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_plans.id", ondelete="CASCADE"))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    assignment_status: Mapped[PlanOrderAssignmentStatus] = mapped_column(
        _enum(PlanOrderAssignmentStatus, length=16, name="plan_order_assignment_status")
    )
    vehicle_route_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    unassigned_reason_code: Mapped[str | None] = mapped_column(String(32))
    unassigned_reason_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    delivery_plan: Mapped[DeliveryPlan] = relationship(back_populates="plan_orders", overlaps="plan_orders,vehicle_route")
    order: Mapped[Order] = relationship(back_populates="plan_orders")
    vehicle_route: Mapped[VehicleRoute | None] = relationship(back_populates="plan_orders", overlaps="delivery_plan,plan_orders")


class RouteStop(Base):
    __tablename__ = "route_stops"
    __table_args__ = (
        UniqueConstraint("vehicle_route_id", "sequence_no", name="uq_route_stops_route_sequence"),
        UniqueConstraint("vehicle_route_id", "order_id", "stop_type", name="uq_route_stops_route_order_type"),
        CheckConstraint("sequence_no > 0", name="ck_route_stops_sequence"),
        CheckConstraint("service_seconds >= 0", name="ck_route_stops_service"),
        CheckConstraint("demand_load_units_snapshot > 0", name="ck_route_stops_demand"),
        CheckConstraint("planned_departure_at >= planned_arrival_at", name="ck_route_stops_planned_time"),
        CheckConstraint(
            "actual_departure_at IS NULL OR (actual_arrival_at IS NOT NULL AND actual_departure_at >= actual_arrival_at)",
            name="ck_route_stops_actual_time",
        ),
        CheckConstraint(
            "time_window_end_at IS NULL OR (time_window_start_at IS NOT NULL AND time_window_end_at >= time_window_start_at)",
            name="ck_route_stops_window",
        ),
        CheckConstraint(
            "(stop_type = 'DELIVERY' AND precedence_stop_id IS NOT NULL) OR "
            "(stop_type <> 'DELIVERY' AND precedence_stop_id IS NULL)",
            name="ck_route_stops_precedence",
        ),
        CheckConstraint(
            "(stop_type = 'HANDOVER' AND source_incident_id IS NOT NULL) OR "
            "(stop_type <> 'HANDOVER' AND source_incident_id IS NULL)",
            name="ck_route_stops_handover_incident",
        ),
        CheckConstraint("stop_type IN ('PICKUP','DELIVERY','HANDOVER')", name="ck_route_stops_type"),
        CheckConstraint("status IN ('PLANNED','ARRIVED','IN_SERVICE','COMPLETED')", name="ck_route_stops_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    vehicle_route_id: Mapped[UUID] = mapped_column(ForeignKey("vehicle_routes.id", ondelete="CASCADE"))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    stop_type: Mapped[StopType] = mapped_column(_enum(StopType, length=16, name="route_stop_type"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    precedence_stop_id: Mapped[UUID | None] = mapped_column(ForeignKey("route_stops.id", ondelete="RESTRICT"))
    source_incident_id: Mapped[UUID | None] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    planned_arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    planned_departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_seconds: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    time_window_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_window_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    demand_load_units_snapshot: Mapped[int] = mapped_column(Integer)
    status: Mapped[StopStatus] = mapped_column(
        _enum(StopStatus, length=16, name="route_stop_status"), server_default=text("'PLANNED'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    vehicle_route: Mapped[VehicleRoute] = relationship(back_populates="stops")
    order: Mapped[Order] = relationship(back_populates="route_stops")
    location: Mapped[Location] = relationship(back_populates="route_stops")
    precedence_stop: Mapped[RouteStop | None] = relationship(remote_side=[id], back_populates="dependent_stops")
    dependent_stops: Mapped[list[RouteStop]] = relationship(back_populates="precedence_stop")
    source_incident: Mapped[Incident | None] = relationship(back_populates="handover_stops")
