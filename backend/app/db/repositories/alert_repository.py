"""Queries and cursor-safe change writes; transaction ownership stays with services."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.models.alerts import AlertStatus, RiskAlert, RiskAlertChange


class AlertRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_active(self, plan_id: UUID, order_id: UUID, risk_type: str) -> RiskAlert | None:
        return self.session.scalar(
            select(RiskAlert).where(
                RiskAlert.delivery_plan_id == plan_id,
                RiskAlert.order_id == order_id,
                RiskAlert.risk_type == risk_type,
                RiskAlert.status == AlertStatus.ACTIVE,
            ).with_for_update()
        )

    def list_active_for_business_date(self, business_date: date) -> list[RiskAlert]:
        return list(self.session.scalars(
            select(RiskAlert).where(
                RiskAlert.business_date == business_date, RiskAlert.status == AlertStatus.ACTIVE
            ).order_by(RiskAlert.detected_at, RiskAlert.id)
        ))

    def latest_evaluation_at(self, business_date: date) -> datetime | None:
        return self.session.scalar(
            select(func.max(RiskAlert.last_evaluated_at)).where(
                RiskAlert.business_date == business_date
            )
        )

    def lock_active_for_business_date(self, business_date: date) -> list[RiskAlert]:
        return list(self.session.scalars(
            select(RiskAlert).where(
                RiskAlert.business_date == business_date, RiskAlert.status == AlertStatus.ACTIVE
            ).order_by(RiskAlert.delivery_plan_id, RiskAlert.order_id, RiskAlert.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ))

    def append_change(self, change: RiskAlertChange) -> int:
        if not inspect(change).transient or change.change_id is not None:
            raise ValueError("Change must be new and have no cursor")
        # Transaction-level lock orders cursor allocation and commits across all writers.
        with self.session.no_autoflush:
            self.session.execute(text("SELECT pg_advisory_xact_lock(55120, 1)"))
            cursor = self.session.scalar(text("SELECT nextval('public.risk_alert_change_cursor_seq'::regclass)"))
        change.change_id = int(cursor)
        self.session.add(change)
        self.session.flush()
        return change.change_id

    def list_alerts(
        self, business_date: date | None, status: str, page: int, page_size: int,
    ) -> tuple[list[RiskAlert], int]:
        statement = select(RiskAlert)
        count = select(func.count()).select_from(RiskAlert)
        if business_date is not None:
            statement = statement.where(RiskAlert.business_date == business_date)
            count = count.where(RiskAlert.business_date == business_date)
        if status:
            statement = statement.where(RiskAlert.status == status)
            count = count.where(RiskAlert.status == status)
        total = int(self.session.scalar(count) or 0)
        items = list(self.session.scalars(
            statement.order_by(RiskAlert.detected_at.desc(), RiskAlert.id)
            .offset((page - 1) * page_size).limit(page_size)
        ))
        return items, total

    def list_changes(self, after: int, limit: int) -> list[RiskAlertChange]:
        return list(self.session.scalars(
            select(RiskAlertChange).where(RiskAlertChange.change_id > after)
            .order_by(RiskAlertChange.change_id).limit(limit)
        ))
