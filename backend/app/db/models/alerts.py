"""P1 delivery-window alert records; schema is created by versioned SQL only."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RiskType(StrEnum):
    DELIVERY_WINDOW = "DELIVERY_WINDOW"


class AlertStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


class AlertChangeType(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    RESOLVED = "RESOLVED"


def _enum(kind: type[StrEnum], name: str, length: int) -> Enum:
    return Enum(kind, name=name, native_enum=False, create_constraint=False, validate_strings=True, length=length)


class RiskAlert(Base):
    __tablename__ = "risk_alerts"
    __table_args__ = (
        CheckConstraint("risk_type = 'DELIVERY_WINDOW'", name="ck_risk_alerts_type"),
        CheckConstraint("status IN ('ACTIVE', 'RESOLVED')", name="ck_risk_alerts_status"),
        CheckConstraint(
            "last_evaluated_at >= detected_at AND (resolved_at IS NULL OR resolved_at >= detected_at) "
            "AND ((status = 'ACTIVE' AND resolved_at IS NULL) OR (status = 'RESOLVED' AND resolved_at IS NOT NULL))",
            name="ck_risk_alerts_times",
        ),
        Index(
            "uq_risk_alerts_active_plan_order_type", "delivery_plan_id", "order_id", "risk_type",
            unique=True, postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_risk_alerts_business_date_status", "business_date", "status", text("detected_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    delivery_plan_id: Mapped[UUID] = mapped_column(ForeignKey("delivery_plans.id", ondelete="RESTRICT"))
    order_id: Mapped[UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    business_date: Mapped[date] = mapped_column(Date)
    risk_type: Mapped[RiskType] = mapped_column(_enum(RiskType, "risk_alert_type", 32))
    status: Mapped[AlertStatus] = mapped_column(_enum(AlertStatus, "risk_alert_status", 16))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    changes: Mapped[list[RiskAlertChange]] = relationship(back_populates="alert", order_by="RiskAlertChange.change_id")


class RiskAlertChange(Base):
    __tablename__ = "risk_alert_changes"
    __table_args__ = (
        CheckConstraint("change_type IN ('CREATED', 'UPDATED', 'RESOLVED')", name="ck_risk_alert_changes_type"),
        Index("ix_risk_alert_changes_alert", "alert_id", "change_id"),
    )

    # No server default: allocation is exclusively through AlertRepository.append_change.
    change_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    alert_id: Mapped[UUID] = mapped_column(ForeignKey("risk_alerts.id", ondelete="RESTRICT"))
    change_type: Mapped[AlertChangeType] = mapped_column(_enum(AlertChangeType, "risk_alert_change_type", 16))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)

    alert: Mapped[RiskAlert] = relationship(back_populates="changes")
