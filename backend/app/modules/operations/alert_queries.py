"""Materialized read models for persisted delivery-window alerts."""

from dataclasses import dataclass
from collections import Counter
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories.alert_repository import AlertRepository


@dataclass(frozen=True)
class AlertView:
    id: UUID
    delivery_plan_id: UUID
    order_id: UUID
    vehicle_route_id: UUID | None
    business_date: date
    risk_type: str
    status: str
    reason_category: str | None
    evidence: dict[str, Any]
    detected_at: datetime
    last_evaluated_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class AlertChangeView:
    change_id: int
    alert_id: UUID
    delivery_plan_id: UUID
    order_id: UUID
    vehicle_route_id: UUID | None
    business_date: date
    risk_type: str
    change_type: str
    reason_category: str | None
    recorded_at: datetime
    evidence_snapshot: dict[str, Any]


@dataclass(frozen=True)
class AlertSummaryView:
    active_count: int
    reason_counts: dict[str, int]
    as_of: datetime | None


class AlertQueryService:
    def __init__(self, session: Session) -> None:
        self.alerts = AlertRepository(session)

    def list_alerts(
        self, business_date: date | None, status: str, page: int, page_size: int,
    ) -> tuple[list[AlertView], int]:
        rows, total = self.alerts.list_alerts(business_date, status, page, page_size)
        return [self._alert_view(row) for row in rows], total

    def active_summary(self, business_date: date) -> AlertSummaryView:
        rows = self.alerts.list_active_for_business_date(business_date)
        reasons = Counter(
            row.evidence.get("reason_category") or "UNSPECIFIED" for row in rows
        )
        return AlertSummaryView(
            active_count=len(rows),
            reason_counts=dict(sorted(reasons.items())),
            as_of=self.alerts.latest_evaluation_at(business_date),
        )

    def get_alert(self, alert_id: UUID) -> AlertView | None:
        row = self.alerts.get_alert(alert_id)
        return self._alert_view(row) if row is not None else None

    def list_alerts_for_order(
        self, business_date: date, order_id: UUID,
    ) -> list[AlertView]:
        return [
            self._alert_view(row)
            for row in self.alerts.list_alerts_for_order(business_date, order_id)
        ]

    def list_changes_for_alert(self, alert_id: UUID) -> list[AlertChangeView]:
        return [
            self._change_view(row)
            for row in self.alerts.list_changes_for_alert(alert_id)
        ]

    def list_changes(self, after: int, limit: int) -> list[AlertChangeView]:
        return [self._change_view(row) for row in self.alerts.list_changes(after, limit)]

    @staticmethod
    def _alert_view(row) -> AlertView:
        return AlertView(
            id=row.id, delivery_plan_id=row.delivery_plan_id,
            order_id=row.order_id,
            vehicle_route_id=(UUID(row.evidence["vehicle_route_id"])
                              if row.evidence.get("vehicle_route_id") else None),
            business_date=row.business_date, risk_type=row.risk_type,
            status=row.status, reason_category=row.evidence.get("reason_category"),
            evidence=dict(row.evidence), detected_at=row.detected_at,
            last_evaluated_at=row.last_evaluated_at, resolved_at=row.resolved_at,
        )

    @staticmethod
    def _change_view(row) -> AlertChangeView:
        return AlertChangeView(
            change_id=row.change_id, alert_id=row.alert_id,
            delivery_plan_id=row.alert.delivery_plan_id,
            order_id=row.alert.order_id,
            vehicle_route_id=(UUID(row.evidence_snapshot["vehicle_route_id"])
                              if row.evidence_snapshot.get("vehicle_route_id") else None),
            business_date=row.alert.business_date,
            risk_type=row.alert.risk_type, change_type=row.change_type,
            reason_category=row.evidence_snapshot.get("reason_category"),
            recorded_at=row.recorded_at,
            evidence_snapshot=dict(row.evidence_snapshot),
        )
