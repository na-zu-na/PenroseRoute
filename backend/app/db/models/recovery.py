from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.planning import ValidationStatus
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus

if TYPE_CHECKING:
    from app.db.models.fleet import Vehicle
    from app.db.models.planning import DeliveryPlan, RouteStop, VehicleRoute
    from app.db.models.resources import Location, Merchant, Order


def _enum(enum: type[StrEnum], *, length: int, name: str) -> Enum:
    return Enum(enum, name=name, native_enum=False, create_constraint=False, validate_strings=True, length=length)


class IncidentType(StrEnum):
    VEHICLE_UNAVAILABLE = "VEHICLE_UNAVAILABLE"
    MERCHANT_DELAY = "MERCHANT_DELAY"


class IncidentStatus(StrEnum):
    DETECTED = "DETECTED"
    ASSESSING = "ASSESSING"
    REPLANNING = "REPLANNING"
    REVIEW = "REVIEW"
    RESOLVED = "RESOLVED"


class ImpactType(StrEnum):
    COMPLETED_FROZEN = "COMPLETED_FROZEN"
    HANDOVER_REQUIRED = "HANDOVER_REQUIRED"
    PICKUP_REPLAN = "PICKUP_REPLAN"
    WAITING_TIME_UPDATE = "WAITING_TIME_UPDATE"
    DOWNSTREAM_ROUTE_IMPACT = "DOWNSTREAM_ROUTE_IMPACT"


class RecoveryPlanStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    DECIDED = "DECIDED"


class ReplanningScope(StrEnum):
    AFFECTED_ROUTE = "AFFECTED_ROUTE"
    CROSS_ROUTE = "CROSS_ROUTE"
    ALL_REMAINING = "ALL_REMAINING"


class SolverStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    ERROR = "ERROR"


class DispatcherDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    MODIFY = "MODIFY"


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("delay_seconds IS NULL OR delay_seconds >= 0", name="ck_incidents_delay"),
        CheckConstraint(
            "incident_type <> 'MERCHANT_DELAY' OR delay_seconds = extract(epoch from (updated_ready_at - original_ready_at))::integer",
            name="ck_incidents_delay_value",
        ),
        CheckConstraint("(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name="ck_incidents_resolution"),
        CheckConstraint("resolved_at IS NULL OR resolved_at >= detected_at", name="ck_incidents_resolution_time"),
        CheckConstraint("incident_type IN ('VEHICLE_UNAVAILABLE','MERCHANT_DELAY')", name="ck_incidents_type"),
        CheckConstraint(
            "status IN ('DETECTED','ASSESSING','REPLANNING','REVIEW','RESOLVED')",
            name="ck_incidents_status",
        ),
        CheckConstraint(
            "(incident_type = 'VEHICLE_UNAVAILABLE' AND vehicle_route_id IS NOT NULL AND vehicle_id IS NOT NULL "
            "AND incident_location_id IS NOT NULL AND merchant_id IS NULL AND original_ready_at IS NULL "
            "AND updated_ready_at IS NULL AND delay_seconds IS NULL) OR "
            "(incident_type = 'MERCHANT_DELAY' AND merchant_id IS NOT NULL AND vehicle_route_id IS NULL "
            "AND vehicle_id IS NULL AND incident_location_id IS NULL AND original_ready_at IS NOT NULL "
            "AND updated_ready_at IS NOT NULL AND delay_seconds IS NOT NULL)",
            name="ck_incidents_type_fields",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    incident_code: Mapped[str] = mapped_column(String(64), unique=True)
    incident_type: Mapped[IncidentType] = mapped_column(_enum(IncidentType, length=32, name="incident_type"))
    status: Mapped[IncidentStatus] = mapped_column(
        _enum(IncidentStatus, length=16, name="incident_status"), server_default=text("'DETECTED'")
    )
    delivery_plan_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_plans.id", ondelete="RESTRICT"))
    vehicle_route_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicle_routes.id", ondelete="RESTRICT"))
    vehicle_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"))
    merchant_id: Mapped[UUID | None] = mapped_column(ForeignKey("merchants.id", ondelete="RESTRICT"))
    incident_location_id: Mapped[UUID | None] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    original_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delay_seconds: Mapped[int | None] = mapped_column(Integer)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_by: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    delivery_plan: Mapped[DeliveryPlan] = relationship(back_populates="incidents")
    vehicle_route: Mapped[VehicleRoute | None] = relationship(back_populates="incidents")
    vehicle: Mapped[Vehicle | None] = relationship(back_populates="incidents")
    merchant: Mapped[Merchant | None] = relationship(back_populates="incidents")
    incident_location: Mapped[Location | None] = relationship(back_populates="incidents")
    affected_orders: Mapped[list[IncidentAffectedOrder]] = relationship(back_populates="incident")
    recovery_plans: Mapped[list[RecoveryPlan]] = relationship(back_populates="incident", order_by="RecoveryPlan.attempt_no")
    handover_stops: Mapped[list[RouteStop]] = relationship(back_populates="source_incident")


class IncidentAffectedOrder(Base):
    __tablename__ = "incident_affected_orders"
    __table_args__ = (
        UniqueConstraint("incident_id", "order_id", name="uq_incident_affected_orders_incident_order"),
        CheckConstraint("was_completed = (execution_status_snapshot = 'COMPLETED')", name="ck_affected_orders_completed"),
        CheckConstraint(
            "was_picked_up = (execution_status_snapshot IN ('PICKED_UP','DELIVERING','COMPLETED'))",
            name="ck_affected_orders_picked_up",
        ),
        CheckConstraint(
            "NOT handover_required OR (was_picked_up AND NOT was_completed AND requires_replanning)",
            name="ck_affected_orders_handover",
        ),
        CheckConstraint(
            "impact_type <> 'COMPLETED_FROZEN' OR (was_completed AND NOT requires_replanning AND NOT handover_required)",
            name="ck_affected_orders_frozen",
        ),
        CheckConstraint(
            "execution_status_snapshot IN ('PLANNED','PICKUP_IN_PROGRESS','PICKED_UP','DELIVERING','COMPLETED')",
            name="ck_affected_orders_execution",
        ),
        CheckConstraint("risk_status_snapshot IN ('NORMAL','AT_RISK')", name="ck_affected_orders_risk"),
        CheckConstraint(
            "impact_type IN ('COMPLETED_FROZEN','HANDOVER_REQUIRED','PICKUP_REPLAN','WAITING_TIME_UPDATE','DOWNSTREAM_ROUTE_IMPACT')",
            name="ck_affected_orders_impact",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    original_vehicle_route_id: Mapped[UUID | None] = mapped_column(ForeignKey("vehicle_routes.id", ondelete="RESTRICT"))
    execution_status_snapshot: Mapped[OrderExecutionStatus] = mapped_column(
        _enum(OrderExecutionStatus, length=32, name="affected_order_execution_status")
    )
    risk_status_snapshot: Mapped[OrderRiskStatus] = mapped_column(
        _enum(OrderRiskStatus, length=16, name="affected_order_risk_status")
    )
    was_picked_up: Mapped[bool] = mapped_column(Boolean)
    was_completed: Mapped[bool] = mapped_column(Boolean)
    requires_replanning: Mapped[bool] = mapped_column(Boolean)
    handover_required: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    impact_type: Mapped[ImpactType] = mapped_column(_enum(ImpactType, length=32, name="impact_type"))
    impact_reason: Mapped[str] = mapped_column(Text)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    incident: Mapped[Incident] = relationship(back_populates="affected_orders")
    order: Mapped[Order] = relationship(back_populates="incident_impacts")
    original_vehicle_route: Mapped[VehicleRoute | None] = relationship(back_populates="incident_impacts")


class RecoveryPlan(Base):
    __tablename__ = "recovery_plans"
    __table_args__ = (
        UniqueConstraint("incident_id", "attempt_no", name="uq_recovery_plans_incident_attempt"),
        CheckConstraint("attempt_no > 0", name="ck_recovery_plans_attempt"),
        CheckConstraint(
            "((attempt_no = 1 AND previous_recovery_plan_id IS NULL) OR "
            "(attempt_no > 1 AND previous_recovery_plan_id IS NOT NULL))",
            name="ck_recovery_plans_previous",
        ),
        CheckConstraint(
            "candidate_delivery_plan_id IS NULL OR candidate_delivery_plan_id <> base_delivery_plan_id",
            name="ck_recovery_plans_distinct_plan",
        ),
        CheckConstraint(
            "candidate_delivery_plan_id IS NULL OR (solver_status = 'FEASIBLE' AND validation_status = 'VALID')",
            name="ck_recovery_plans_candidate",
        ),
        CheckConstraint(
            "status <> 'DRAFT' OR (dispatcher_decision IS NULL AND decision_reason IS NULL "
            "AND reviewed_by IS NULL AND reviewed_at IS NULL)",
            name="ck_recovery_plans_draft_decision",
        ),
        CheckConstraint(
            "status <> 'PENDING_REVIEW' OR (candidate_delivery_plan_id IS NOT NULL "
            "AND btrim(coalesce(agent_explanation, '')) <> '' AND solver_status = 'FEASIBLE' "
            "AND validation_status = 'VALID' AND dispatcher_decision IS NULL AND decision_reason IS NULL "
            "AND reviewed_by IS NULL AND reviewed_at IS NULL)",
            name="ck_recovery_plans_pending_review",
        ),
        CheckConstraint(
            "status <> 'DECIDED' OR (candidate_delivery_plan_id IS NOT NULL "
            "AND btrim(coalesce(agent_explanation, '')) <> '' AND solver_status = 'FEASIBLE' "
            "AND validation_status = 'VALID' AND dispatcher_decision IS NOT NULL "
            "AND btrim(coalesce(decision_reason, '')) <> '' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_recovery_plans_decided",
        ),
        CheckConstraint(
            "(solver_status IS NULL AND validation_status IS NULL AND candidate_delivery_plan_id IS NULL) OR "
            "(solver_status IN ('INFEASIBLE','ERROR') AND validation_status IS NULL "
            "AND candidate_delivery_plan_id IS NULL AND status = 'DRAFT') OR "
            "(solver_status = 'FEASIBLE' AND validation_status IN ('PENDING','INVALID') "
            "AND candidate_delivery_plan_id IS NULL) OR "
            "(solver_status = 'FEASIBLE' AND validation_status = 'VALID')",
            name="ck_recovery_plans_solver_outcome",
        ),
        CheckConstraint("status IN ('DRAFT','PENDING_REVIEW','DECIDED')", name="ck_recovery_plans_status"),
        CheckConstraint(
            "replanning_scope IN ('AFFECTED_ROUTE','CROSS_ROUTE','ALL_REMAINING')",
            name="ck_recovery_plans_scope",
        ),
        CheckConstraint(
            "solver_status IS NULL OR solver_status IN ('FEASIBLE','INFEASIBLE','ERROR')",
            name="ck_recovery_plans_solver",
        ),
        CheckConstraint(
            "validation_status IS NULL OR validation_status IN ('PENDING','VALID','INVALID')",
            name="ck_recovery_plans_validation",
        ),
        CheckConstraint(
            "dispatcher_decision IS NULL OR dispatcher_decision IN ('APPROVE','REJECT','MODIFY')",
            name="ck_recovery_plans_decision",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    recovery_code: Mapped[str] = mapped_column(String(64), unique=True)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    previous_recovery_plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("recovery_plans.id", ondelete="RESTRICT"))
    base_delivery_plan_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_plans.id", ondelete="RESTRICT"))
    candidate_delivery_plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("delivery_plans.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[RecoveryPlanStatus] = mapped_column(
        _enum(RecoveryPlanStatus, length=24, name="recovery_plan_status"), server_default=text("'DRAFT'")
    )
    replanning_scope: Mapped[ReplanningScope] = mapped_column(
        _enum(ReplanningScope, length=24, name="replanning_scope")
    )
    scope_description: Mapped[str] = mapped_column(Text)
    agent_explanation: Mapped[str | None] = mapped_column(Text)
    solver_status: Mapped[SolverStatus | None] = mapped_column(
        _enum(SolverStatus, length=16, name="solver_status")
    )
    validation_status: Mapped[ValidationStatus | None] = mapped_column(
        _enum(ValidationStatus, length=16, name="recovery_validation_status")
    )
    solver_validation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dispatcher_decision: Mapped[DispatcherDecision | None] = mapped_column(
        _enum(DispatcherDecision, length=16, name="dispatcher_decision")
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    incident: Mapped[Incident] = relationship(back_populates="recovery_plans")
    previous_recovery_plan: Mapped[RecoveryPlan | None] = relationship(remote_side=[id], back_populates="next_recovery_plans")
    next_recovery_plans: Mapped[list[RecoveryPlan]] = relationship(back_populates="previous_recovery_plan")
    base_delivery_plan: Mapped[DeliveryPlan] = relationship(
        back_populates="base_recovery_plans", foreign_keys=[base_delivery_plan_id]
    )
    candidate_delivery_plan: Mapped[DeliveryPlan | None] = relationship(
        back_populates="candidate_recovery_plan", foreign_keys=[candidate_delivery_plan_id]
    )
