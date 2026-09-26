"""Task 4 alert lifecycle against disposable P0→P1 PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event, current_thread
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import DeliveryPlan, Order, RouteStop
from app.db.models.alerts import AlertStatus, RiskAlert, RiskAlertChange
from app.db.models.planning import DeliveryPlanStatus
from app.db.models.resources import OrderRiskStatus
from app.db.repositories.plan_repository import PlanRepository
from app.modules.decisions.service import DeterministicDecisionService
from tests.database.test_p1_alert_migration import _runner


DAY = date(2026, 9, 25)
TZ = timezone(timedelta(hours=8))
AT_RISK = datetime(2026, 9, 25, 10, 5, tzinfo=TZ)
EARLY = datetime(2026, 9, 25, 9, 15, tzinfo=TZ)
BASE = UUID("80000000-0000-0000-0000-000000000001")
CANDIDATE = UUID("80000000-0000-0000-0000-000000000002")
RECOVERY = UUID("e0000000-0000-0000-0000-000000000001")
ORDER_3 = UUID("40000000-0000-0000-0000-000000000003")
ORDER_1 = UUID("40000000-0000-0000-0000-000000000001")


@pytest.fixture
def alert_engine(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    engine = create_engine(p1_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def evaluate(engine, at):
    from app.modules.operations.alerts import AlertService

    with Session(engine) as session:
        return AlertService(session).evaluate_business_date(DAY, at)


def alert_rows(engine, order_id=ORDER_3):
    with Session(engine) as session:
        return list(session.scalars(
            select(RiskAlert).where(RiskAlert.order_id == order_id).order_by(RiskAlert.detected_at)
        ))


def changes(engine, alert_id):
    with Session(engine) as session:
        return list(session.scalars(
            select(RiskAlertChange).where(RiskAlertChange.alert_id == alert_id)
            .order_by(RiskAlertChange.change_id)
        ))


def test_normal_to_at_risk_creates_alert_and_change_without_touching_completed_order(alert_engine):
    with Session(alert_engine) as session, session.begin():
        session.get(Order, ORDER_3).risk_status = OrderRiskStatus.NORMAL

    result = evaluate(alert_engine, AT_RISK)

    assert result.current_plan_id == BASE
    rows = alert_rows(alert_engine)
    assert len(rows) == 1
    assert rows[0].status is AlertStatus.ACTIVE
    assert rows[0].detected_at == AT_RISK
    assert rows[0].evidence["reason_category"] == "APPROACHING_WINDOW"
    assert [item.change_type for item in changes(alert_engine, rows[0].id)] == ["CREATED"]
    assert alert_rows(alert_engine, ORDER_1) == []
    with Session(alert_engine) as session:
        assert session.get(Order, ORDER_3).risk_status is OrderRiskStatus.AT_RISK
        assert session.get(Order, ORDER_1).risk_status is OrderRiskStatus.NORMAL


def test_seeded_at_risk_without_alert_is_discovered_now_and_repeat_does_not_flood(alert_engine):
    # ORD-003 is already AT_RISK in P0 seed; this is first P1 detection.
    evaluate(alert_engine, AT_RISK)
    row = alert_rows(alert_engine)[0]
    again = AT_RISK + timedelta(minutes=1)
    evaluate(alert_engine, again)

    rows = alert_rows(alert_engine)
    assert len(rows) == 1
    assert rows[0].id == row.id
    assert rows[0].detected_at == AT_RISK
    assert rows[0].last_evaluated_at == again
    assert [item.change_type for item in changes(alert_engine, row.id)] == ["CREATED"]


def test_backdated_evaluation_does_not_regress_active_alert(alert_engine):
    evaluate(alert_engine, AT_RISK)
    row = alert_rows(alert_engine)[0]

    result = evaluate(alert_engine, AT_RISK - timedelta(minutes=5))

    assert result.created_count == result.updated_count == result.resolved_count == 0
    current = alert_rows(alert_engine)[0]
    assert current.id == row.id
    assert current.status is AlertStatus.ACTIVE
    assert current.last_evaluated_at == AT_RISK
    assert [item.change_type for item in changes(alert_engine, row.id)] == ["CREATED"]


def test_backdated_event_refreshes_new_facts_at_monotonic_time(alert_engine):
    evaluate(alert_engine, AT_RISK)
    with Session(alert_engine) as session, session.begin():
        for stop_id in (5, 6):
            session.get(RouteStop, UUID(f"c0000000-0000-0000-0000-{stop_id:012d}")).status = "COMPLETED"

    result = evaluate(alert_engine, AT_RISK - timedelta(minutes=5))

    assert result.resolved_count >= 1
    row = alert_rows(alert_engine)[0]
    assert row.status is AlertStatus.RESOLVED
    assert row.resolved_at == AT_RISK


def test_backdated_evaluation_does_not_reopen_resolved_alert(alert_engine):
    evaluate(alert_engine, AT_RISK)
    with Session(alert_engine) as session, session.begin():
        session.get(Order, ORDER_3).execution_status = "COMPLETED"
    evaluate(alert_engine, AT_RISK + timedelta(minutes=1))

    result = evaluate(alert_engine, AT_RISK - timedelta(minutes=1))

    assert result.created_count == result.updated_count == result.resolved_count == 0
    rows = alert_rows(alert_engine)
    assert len(rows) == 1
    assert rows[0].status is AlertStatus.RESOLVED


def test_reason_transition_updates_same_alert_and_appends_one_change(alert_engine):
    evaluate(alert_engine, AT_RISK)
    row = alert_rows(alert_engine)[0]
    later = AT_RISK + timedelta(minutes=15)
    evaluate(alert_engine, later)
    evaluate(alert_engine, later + timedelta(seconds=1))

    current = alert_rows(alert_engine)[0]
    assert current.id == row.id
    assert current.evidence["reason_category"] == "PREDICTED_MISS"
    assert [item.change_type for item in changes(alert_engine, row.id)] == ["CREATED", "UPDATED"]


def test_progress_clears_risk_and_resolves_alert(alert_engine):
    evaluate(alert_engine, AT_RISK)
    row = alert_rows(alert_engine)[0]
    with Session(alert_engine) as session, session.begin():
        # Both earlier pickups finish; the next task is the delivery at 10:25.
        for stop_id in (5, 6):
            stop = session.get(RouteStop, UUID(f"c0000000-0000-0000-0000-{stop_id:012d}"))
            stop.status = "COMPLETED"
            stop.actual_arrival_at = AT_RISK - timedelta(minutes=2)
            stop.actual_departure_at = AT_RISK - timedelta(minutes=1)
    cleared_at = AT_RISK + timedelta(minutes=1)
    evaluate(alert_engine, cleared_at)

    resolved = alert_rows(alert_engine)[0]
    assert resolved.status is AlertStatus.RESOLVED
    assert resolved.resolved_at == cleared_at
    assert [item.change_type for item in changes(alert_engine, row.id)] == ["CREATED", "RESOLVED"]
    with Session(alert_engine) as session:
        assert session.get(Order, ORDER_3).risk_status is OrderRiskStatus.NORMAL


def test_completed_order_resolves_existing_alert(alert_engine):
    evaluate(alert_engine, AT_RISK)
    row = alert_rows(alert_engine)[0]
    with Session(alert_engine) as session, session.begin():
        session.get(Order, ORDER_3).execution_status = "COMPLETED"

    result = evaluate(alert_engine, AT_RISK + timedelta(minutes=1))

    assert result.resolved_count >= 1
    assert alert_rows(alert_engine)[0].status is AlertStatus.RESOLVED
    assert [item.change_type for item in changes(alert_engine, row.id)] == [
        "CREATED", "RESOLVED"
    ]
    with Session(alert_engine) as session:
        assert session.get(Order, ORDER_3).risk_status is OrderRiskStatus.NORMAL


def test_no_current_plan_resolves_old_alert_without_treating_candidate_as_current(alert_engine):
    evaluate(alert_engine, AT_RISK)
    with Session(alert_engine) as session, session.begin():
        base = session.get(DeliveryPlan, BASE)
        base.status = DeliveryPlanStatus.SUPERSEDED
        base.superseded_at = AT_RISK

    result = evaluate(alert_engine, AT_RISK + timedelta(minutes=1))

    assert result.current_plan_id is None
    assert alert_rows(alert_engine)[0].status is AlertStatus.RESOLVED
    with Session(alert_engine) as session:
        assert session.get(DeliveryPlan, CANDIDATE).status is DeliveryPlanStatus.CANDIDATE


def test_approval_switch_resolves_base_alert_and_evaluates_new_current(alert_engine):
    evaluate(alert_engine, AT_RISK)
    sessions = sessionmaker(bind=alert_engine, expire_on_commit=False)
    DeterministicDecisionService(sessions, clock=lambda: AT_RISK).decide(
        RECOVERY, "APPROVE", "Candidate accepted", "dispatcher"
    )

    result = evaluate(alert_engine, AT_RISK + timedelta(minutes=1))

    assert result.current_plan_id == CANDIDATE
    assert alert_rows(alert_engine)[0].status is AlertStatus.RESOLVED
    with Session(alert_engine) as session:
        active = list(session.scalars(
            select(RiskAlert).where(RiskAlert.status == AlertStatus.ACTIVE)
        ))
        assert active
        assert all(item.delivery_plan_id == CANDIDATE for item in active)


def test_paused_old_scan_cannot_recreate_alert_after_approval(alert_engine, monkeypatch):
    selected_old = Event()
    continue_scan = Event()
    original_get = PlanRepository.get_current_plan

    def paused_get(self, business_date):
        plan = original_get(self, business_date)
        if current_thread().name.startswith("paused-alert-scan") and plan is not None:
            selected_old.set()
            assert continue_scan.wait(timeout=5)
        return plan

    monkeypatch.setattr(PlanRepository, "get_current_plan", paused_get)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="paused-alert-scan") as pool:
        future = pool.submit(evaluate, alert_engine, AT_RISK + timedelta(minutes=1))
        assert selected_old.wait(timeout=5)
        try:
            sessions = sessionmaker(bind=alert_engine, expire_on_commit=False)
            DeterministicDecisionService(sessions, clock=lambda: AT_RISK).decide(
                RECOVERY, "APPROVE", "Candidate accepted", "dispatcher"
            )
            evaluate(alert_engine, AT_RISK + timedelta(minutes=1))
        finally:
            continue_scan.set()
        result = future.result(timeout=5)

    assert result.current_plan_id == CANDIDATE
    with Session(alert_engine) as session:
        assert not session.scalars(select(RiskAlert).where(
            RiskAlert.delivery_plan_id == BASE, RiskAlert.status == AlertStatus.ACTIVE
        )).first()
