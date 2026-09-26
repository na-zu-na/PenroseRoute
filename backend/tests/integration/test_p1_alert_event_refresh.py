"""Post-commit alert refresh through real P0 application commands."""

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import Conflict
from app.db.models import DeliveryPlan, Merchant, Order, RouteStop, Vehicle
from app.db.models.alerts import AlertStatus, RiskAlert
from app.db.models.planning import DeliveryPlanStatus, StopStatus
from app.db.models.fleet import ResourceStatus
from app.modules.decisions.service import DeterministicDecisionService
from app.modules.incidents.workflow import MerchantDelayWorkflow, VehicleIncidentWorkflow
from app.modules.operations.alerts import AlertService
from app.modules.operations.alert_refresh import refresh_alerts_for_business_date
from app.modules.operations.execution import StopExecutionService
from tests.database.test_p1_alert_migration import _runner


DAY = date(2026, 9, 25)
TZ = timezone(timedelta(hours=8))
AT = datetime(2026, 9, 25, 10, 5, tzinfo=TZ)
BASE = UUID("80000000-0000-0000-0000-000000000001")
CANDIDATE = UUID("80000000-0000-0000-0000-000000000002")
RECOVERY = UUID("e0000000-0000-0000-0000-000000000001")
MERCHANT = UUID("20000000-0000-0000-0000-000000000001")
VEHICLE = UUID("50000000-0000-0000-0000-000000000002")
FIRST_STOP = UUID("c0000000-0000-0000-0000-000000000005")
SECOND_STOP = UUID("c0000000-0000-0000-0000-000000000006")
MERCHANT_PICKUP = UUID("c0000000-0000-0000-0000-000000000008")
ORDER = UUID("40000000-0000-0000-0000-000000000003")


@pytest.fixture
def alert_engine(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    engine = create_engine(p1_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def active_alerts(engine):
    with Session(engine) as session:
        return list(session.scalars(select(RiskAlert).where(RiskAlert.status == AlertStatus.ACTIVE)))


def test_stop_action_refreshes_alert_after_commit_without_alert_get(alert_engine):
    with Session(alert_engine) as session:
        StopExecutionService(session).arrive(FIRST_STOP, AT)

    with Session(alert_engine) as session:
        assert session.get(RouteStop, FIRST_STOP).status is StopStatus.ARRIVED
    assert any(alert.order_id == ORDER and alert.delivery_plan_id == BASE for alert in active_alerts(alert_engine))


def test_failed_stop_action_does_not_write_alert(alert_engine):
    with Session(alert_engine) as session:
        with pytest.raises(Conflict):
            StopExecutionService(session).arrive(SECOND_STOP, AT)

    assert active_alerts(alert_engine) == []
    with Session(alert_engine) as session:
        assert session.get(RouteStop, SECOND_STOP).status is StopStatus.PLANNED


def test_merchant_ready_time_refreshes_alert_without_switching_candidate(alert_engine):
    with Session(alert_engine) as session, session.begin():
        session.get(RouteStop, MERCHANT_PICKUP).time_window_start_at = datetime(2026, 9, 25, 10, tzinfo=TZ)

    with Session(alert_engine) as session:
        result = MerchantDelayWorkflow(session).assess_delay(
            business_date=DAY,
            merchant_id=MERCHANT,
            updated_ready_at=datetime(2026, 9, 25, 10, 10, tzinfo=TZ),
            detected_at=AT,
            explicit_incident=False,
        )

    assert result.delay_seconds == 600
    assert result.requires_replanning is False
    assert active_alerts(alert_engine)
    with Session(alert_engine) as session:
        assert session.get(DeliveryPlan, BASE).status is DeliveryPlanStatus.CURRENT
        assert session.get(DeliveryPlan, CANDIDATE).status is DeliveryPlanStatus.CANDIDATE
        assert session.get(Merchant, MERCHANT).operational_ready_at == datetime(2026, 9, 25, 10, 10, tzinfo=TZ)


def test_vehicle_unavailable_incident_refreshes_alert(alert_engine):
    with Session(alert_engine) as session:
        result = VehicleIncidentWorkflow(session).report_unavailable(
            business_date=DAY, vehicle_id=VEHICLE, detected_at=AT,
        )

    assert result.recovery_required is True
    assert active_alerts(alert_engine)
    with Session(alert_engine) as session:
        assert session.get(Vehicle, VEHICLE).status is ResourceStatus.UNAVAILABLE


def test_approval_resolves_base_alert_and_evaluates_new_current(alert_engine):
    with Session(alert_engine) as session:
        AlertService(session).evaluate_business_date(DAY, AT)
    assert any(alert.delivery_plan_id == BASE for alert in active_alerts(alert_engine))

    sessions = sessionmaker(bind=alert_engine, expire_on_commit=False)
    DeterministicDecisionService(sessions, clock=lambda: AT + timedelta(minutes=1)).decide(
        RECOVERY, "APPROVE", "Accept candidate", "dispatcher"
    )

    with Session(alert_engine) as session:
        assert session.get(DeliveryPlan, BASE).status is DeliveryPlanStatus.SUPERSEDED
        assert session.get(DeliveryPlan, CANDIDATE).status is DeliveryPlanStatus.CURRENT
        assert not session.scalars(select(RiskAlert).where(
            RiskAlert.delivery_plan_id == BASE, RiskAlert.status == AlertStatus.ACTIVE
        )).first()
    assert all(alert.delivery_plan_id == CANDIDATE for alert in active_alerts(alert_engine))


def test_refresh_failure_does_not_undo_committed_stop(alert_engine, monkeypatch, caplog):
    def fail_refresh(self, business_date, now):
        raise RuntimeError("simulated alert storage outage")

    with monkeypatch.context() as patch:
        patch.setattr(AlertService, "evaluate_business_date", fail_refresh)
        with Session(alert_engine) as session:
            StopExecutionService(session).arrive(FIRST_STOP, AT)

    with Session(alert_engine) as session:
        assert session.get(RouteStop, FIRST_STOP).status is StopStatus.ARRIVED
    assert active_alerts(alert_engine) == []
    assert "simulated alert storage outage" in caplog.text
    with Session(alert_engine) as session:
        AlertService(session).evaluate_business_date(DAY, AT)
    assert active_alerts(alert_engine)


def test_refresh_waits_for_real_commit_not_an_outer_test_transaction(alert_engine, caplog):
    timed_engine = create_engine(
        alert_engine.url, connect_args={"options": "-c statement_timeout=500"}
    )
    try:
        with timed_engine.connect() as connection:
            outer = connection.begin()
            try:
                with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                    session.scalar(select(DeliveryPlan).where(DeliveryPlan.id == BASE).with_for_update())
                    refresh_alerts_for_business_date(session, DAY, AT)
                assert not any("Alert refresh failed" in record.message for record in caplog.records)
            finally:
                outer.rollback()
    finally:
        timed_engine.dispose()
    assert active_alerts(alert_engine) == []
