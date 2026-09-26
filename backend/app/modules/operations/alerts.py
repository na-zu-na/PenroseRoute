"""Deterministic, transactional delivery-window alert evaluation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.alerts import AlertChangeType, AlertStatus, RiskAlert, RiskAlertChange, RiskType
from app.db.models.planning import PlanOrderAssignmentStatus, StopStatus, StopType
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.repositories.alert_repository import AlertRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.modules.operations.risk import RiskService


@dataclass(frozen=True)
class AlertEvaluationResult:
    business_date: date
    current_plan_id: UUID | None
    created_count: int
    updated_count: int
    resolved_count: int


class AlertService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.plans = PlanRepository(session)
        self.resources = ResourceRepository(session)
        self.alerts = AlertRepository(session)
        self.risk = RiskService(threshold_seconds=get_settings().at_risk_threshold_seconds)

    def evaluate_business_date(self, business_date: date, now: datetime) -> AlertEvaluationResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include a timezone offset")
        for retry in range(2):
            try:
                with self.session.begin():
                    return self._evaluate_locked(business_date, now)
            except IntegrityError as error:
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                if constraint != "uq_risk_alerts_active_plan_order_type" or retry:
                    raise
                # A competing writer won the partial-unique race. Retry the
                # whole short transaction, never a failed PostgreSQL Session.
        raise AssertionError("unreachable")

    def _evaluate_locked(self, business_date: date, now: datetime) -> AlertEvaluationResult:
        plan = self.plans.lock_current_plan_for_alerts(business_date)
        if plan is None:
            # With no Current row, lock old alert Plans before execution rows.
            for plan_id in sorted({alert.delivery_plan_id for alert in
                                   self.alerts.list_active_for_business_date(business_date)}):
                self.plans.lock_plan_by_id(plan_id)
            plan = self.plans.lock_current_plan_for_alerts(business_date)

        # A late-reported event must refresh current facts without rewinding
        # persisted alert chronology (including already-resolved alerts).
        latest = self.alerts.latest_evaluation_at(business_date)
        if latest is not None:
            now = max(now, latest)

        active_before = self.alerts.list_active_for_business_date(business_date)
        full = self.plans.get_plan_with_routes(plan.id) if plan else None
        memberships = {item.order_id: item for item in full.plan_orders} if full else {}
        order_ids = sorted(set(memberships) | {alert.order_id for alert in active_before})
        orders = {order.id: order for order in self.resources.lock_orders_by_ids(order_ids)}
        active = {
            (alert.delivery_plan_id, alert.order_id): alert
            for alert in self.alerts.lock_active_for_business_date(business_date)
        }

        created = updated = resolved = 0
        touched = set()
        routes = {route.id: route for route in full.routes} if full else {}
        deliveries = {
            (route.id, stop.order_id): stop
            for route in routes.values()
            for stop in route.stops
            if stop.stop_type is StopType.DELIVERY
        }
        route_delays = {
            route.id: self.risk.route_delay_seconds(stops=route.stops, current_time=now)
            for route in routes.values()
        }
        for order_id, membership in sorted(memberships.items()):
            order = orders.get(order_id)
            if order is None:
                continue
            route = routes.get(membership.vehicle_route_id) if (
                membership.assignment_status is PlanOrderAssignmentStatus.ASSIGNED
            ) else None
            delivery = deliveries.get((route.id, order_id)) if route else None
            if order.execution_status is OrderExecutionStatus.COMPLETED or (
                delivery is not None and delivery.status is StopStatus.COMPLETED
            ):
                order.risk_status = OrderRiskStatus.NORMAL
                continue
            if delivery is None:
                order.risk_status = OrderRiskStatus.NORMAL
                continue

            delay = route_delays[route.id]
            eta = delivery.planned_arrival_at + timedelta(seconds=delay)
            window_end = delivery.time_window_end_at or order.delivery_window_end_at
            status = self.risk.status_for_eta(
                estimated_arrival_at=eta, delivery_window_end_at=window_end,
            )
            order.risk_status = status
            if status is OrderRiskStatus.NORMAL:
                continue

            evidence = {
                "reason_category": self.risk.reason_for_eta(eta, window_end),
                "estimated_arrival_at": eta.isoformat(),
                "delivery_window_end_at": window_end.isoformat(),
                "delay_seconds": delay,
                "threshold_seconds": self.risk.threshold_seconds,
                "vehicle_route_id": str(route.id),
            }
            key = (plan.id, order_id)
            alert = active.get(key)
            if alert is None:
                alert = RiskAlert(
                    delivery_plan_id=plan.id, order_id=order_id,
                    business_date=business_date, risk_type=RiskType.DELIVERY_WINDOW,
                    status=AlertStatus.ACTIVE, evidence=evidence,
                    detected_at=now, last_evaluated_at=now,
                )
                self.session.add(alert)
                self.session.flush()
                self._append(alert, AlertChangeType.CREATED, now)
                created += 1
            else:
                changed = alert.evidence.get("reason_category") != evidence["reason_category"]
                alert.evidence = evidence
                alert.last_evaluated_at = now
                if changed:
                    self._append(alert, AlertChangeType.UPDATED, now)
                    updated += 1
            touched.add(key)

        for key, alert in active.items():
            if key in touched:
                continue
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = now
            alert.last_evaluated_at = now
            reason = "PLAN_NOT_CURRENT" if plan is None or alert.delivery_plan_id != plan.id else "RISK_CLEARED"
            alert.evidence = {**alert.evidence, "reason_category": reason}
            if alert.order_id not in memberships and alert.order_id in orders:
                orders[alert.order_id].risk_status = OrderRiskStatus.NORMAL
            self._append(alert, AlertChangeType.RESOLVED, now)
            resolved += 1

        return AlertEvaluationResult(business_date, plan.id if plan else None, created, updated, resolved)

    def _append(self, alert: RiskAlert, kind: AlertChangeType, now: datetime) -> None:
        self.alerts.append_change(RiskAlertChange(
            alert_id=alert.id, change_type=kind,
            recorded_at=now, evidence_snapshot=dict(alert.evidence),
        ))
