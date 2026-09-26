"""Task 6: autonomous alert scanning against an isolated PostgreSQL database."""

from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread
from time import monotonic, sleep
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.alerts import AlertStatus, RiskAlert
from app.db.models.planning import DeliveryPlan, DeliveryPlanStatus, RouteStop, StopStatus, StopType, VehicleRoute
from app.modules.operations.alerts import AlertService
from tests.database.test_p1_alert_migration import _runner


DAY = date(2026, 9, 25)
TZ = timezone(timedelta(hours=8))
EARLY = datetime(2026, 9, 25, 9, 15, tzinfo=TZ)
RISK_TIME = datetime(2026, 9, 25, 10, 5, tzinfo=TZ)
ORDER = UUID("40000000-0000-0000-0000-000000000003")


@pytest.fixture
def worker_engine(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    engine = create_engine(p1_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _sessions(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _alerts(engine, order_id=ORDER):
    with Session(engine) as session:
        return list(session.scalars(select(RiskAlert).where(RiskAlert.order_id == order_id)))


def _eventually(predicate, timeout=3):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.02)
    assert predicate()


def test_scan_once_discovers_time_only_risk_without_http(worker_engine):
    from app.jobs.alert_worker import scan_once

    result = scan_once(_sessions(worker_engine), RISK_TIME)

    assert result.scanned_dates == (DAY,)
    assert result.created_count > 0
    assert len(_alerts(worker_engine)) == 1
    assert _alerts(worker_engine)[0].status is AlertStatus.ACTIVE


def test_scan_once_skips_date_without_unfinished_work_or_active_alert(worker_engine):
    from app.jobs.alert_worker import scan_once

    with Session(worker_engine) as session, session.begin():
        route_ids = select(VehicleRoute.id).join(DeliveryPlan).where(
            DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
            DeliveryPlan.business_date == DAY,
        )
        session.execute(update(RouteStop).where(
            RouteStop.vehicle_route_id.in_(route_ids), RouteStop.stop_type == StopType.DELIVERY,
        ).values(status=StopStatus.COMPLETED))

    result = scan_once(_sessions(worker_engine), RISK_TIME)

    assert result.scanned_dates == ()
    assert _alerts(worker_engine) == []


def test_scan_once_reconciles_active_alert_after_all_deliveries_finish(worker_engine):
    from app.jobs.alert_worker import scan_once

    sessions = _sessions(worker_engine)
    scan_once(sessions, RISK_TIME)
    with Session(worker_engine) as session, session.begin():
        route_ids = select(VehicleRoute.id).join(DeliveryPlan).where(
            DeliveryPlan.status == DeliveryPlanStatus.CURRENT,
            DeliveryPlan.business_date == DAY,
        )
        session.execute(update(RouteStop).where(
            RouteStop.vehicle_route_id.in_(route_ids), RouteStop.stop_type == StopType.DELIVERY,
        ).values(status=StopStatus.COMPLETED))

    result = scan_once(sessions, RISK_TIME + timedelta(minutes=1))

    assert result.scanned_dates == (DAY,)
    assert result.resolved_count > 0
    assert _alerts(worker_engine)[0].status is AlertStatus.RESOLVED


def test_scan_once_repairs_failed_event_refresh(worker_engine, monkeypatch):
    from app.jobs.alert_worker import scan_once
    from app.modules.operations.execution import StopExecutionService

    stop_id = UUID("c0000000-0000-0000-0000-000000000005")
    with monkeypatch.context() as patch:
        patch.setattr(AlertService, "evaluate_business_date", lambda *args: (_ for _ in ()).throw(RuntimeError("refresh down")))
        with Session(worker_engine) as session:
            StopExecutionService(session).arrive(stop_id, RISK_TIME)
    assert _alerts(worker_engine) == []

    result = scan_once(_sessions(worker_engine), RISK_TIME)

    assert result.created_count > 0
    assert _alerts(worker_engine)[0].status is AlertStatus.ACTIVE


def test_runner_advances_time_and_scans_without_any_request(worker_engine):
    from app.jobs.alert_worker import run_worker

    stop = Event()
    sleeping = Event()
    wake = Event()
    current = [EARLY]

    def controlled_sleep(_seconds):
        sleeping.set()
        wake.wait(timeout=3)
        wake.clear()

    worker = Thread(target=run_worker, args=(_sessions(worker_engine), 30, stop,
                                              lambda: current[0], controlled_sleep), daemon=True)
    worker.start()
    try:
        assert sleeping.wait(timeout=3)
        assert _alerts(worker_engine) == []
        current[0] = RISK_TIME
        wake.set()
        _eventually(lambda: len(_alerts(worker_engine)) == 1)
    finally:
        stop.set()
        wake.set()
        worker.join(timeout=3)
    assert not worker.is_alive()


def test_standby_waits_for_owner_then_takes_over(worker_engine):
    from app.jobs.alert_worker import run_worker

    with worker_engine.connect() as owner:
        assert owner.exec_driver_sql("SELECT pg_try_advisory_lock(55120, 2)").scalar_one()
        owner.commit()
        stop = Event()
        waiting = Event()
        wake = Event()

        def controlled_sleep(_seconds):
            waiting.set()
            wake.wait(timeout=3)
            wake.clear()

        standby = Thread(target=run_worker, args=(_sessions(worker_engine), 30, stop,
                                                    lambda: RISK_TIME, controlled_sleep), daemon=True)
        standby.start()
        try:
            assert waiting.wait(timeout=3)
            assert _alerts(worker_engine) == []
            assert owner.exec_driver_sql("SELECT pg_advisory_unlock(55120, 2)").scalar_one()
            owner.commit()
            wake.set()
            _eventually(lambda: len(_alerts(worker_engine)) == 1)
        finally:
            stop.set()
            wake.set()
            standby.join(timeout=3)
    assert not standby.is_alive()


def test_two_running_workers_never_scan_as_owners_together(worker_engine):
    from app.jobs.alert_worker import run_worker

    first_stop, second_stop = Event(), Event()
    first_sleeping, second_waiting = Event(), Event()
    first_wake, second_wake = Event(), Event()

    def first_sleep(_seconds):
        first_sleeping.set()
        first_wake.wait(timeout=3)
        first_wake.clear()

    def second_sleep(_seconds):
        second_waiting.set()
        second_wake.wait(timeout=3)
        second_wake.clear()

    first = Thread(target=run_worker, args=(_sessions(worker_engine), 30, first_stop,
                                            lambda: EARLY, first_sleep), daemon=True)
    second = Thread(target=run_worker, args=(_sessions(worker_engine), 30, second_stop,
                                             lambda: RISK_TIME, second_sleep), daemon=True)
    first.start()
    try:
        assert first_sleeping.wait(timeout=3)
        second.start()
        assert second_waiting.wait(timeout=3)
        assert _alerts(worker_engine) == []  # The standby's RISK_TIME scan has not run.
        first_stop.set()
        first_wake.set()
        first.join(timeout=3)
        assert not first.is_alive()
        second_wake.set()
        _eventually(lambda: len(_alerts(worker_engine)) == 1)
    finally:
        first_stop.set()
        second_stop.set()
        first_wake.set()
        second_wake.set()
        first.join(timeout=3)
        second.join(timeout=3)
    assert not second.is_alive()


def test_acquired_lock_is_released_if_first_ownership_check_fails(worker_engine, monkeypatch):
    from app.jobs import alert_worker

    stop = Event()

    def fail_check(_connection):
        raise RuntimeError("ownership check unavailable")

    monkeypatch.setattr(alert_worker, "_owner_state", fail_check)
    alert_worker.run_worker(_sessions(worker_engine), 30, stop,
                            lambda: RISK_TIME, lambda _seconds: stop.set())

    with worker_engine.connect() as observer:
        held = observer.exec_driver_sql(
            "SELECT COUNT(*) FROM pg_locks WHERE locktype='advisory' "
            "AND classid=55120 AND objid=2 AND objsubid=2 AND granted"
        ).scalar_one()
    assert held == 0


def test_connection_lost_between_scan_phases_cannot_continue_without_lock(worker_engine, monkeypatch):
    from app.jobs import alert_worker

    stop = Event()
    real_scan = alert_worker.scan_once

    def disconnect_then_scan(sessions, now):
        with sessions() as session:
            session.get_bind().invalidate()
        return real_scan(sessions, now)

    monkeypatch.setattr(alert_worker, "scan_once", disconnect_then_scan)
    alert_worker.run_worker(_sessions(worker_engine), 30, stop,
                            lambda: RISK_TIME, lambda _seconds: stop.set())

    assert _alerts(worker_engine) == []


def test_lost_owner_connection_must_reacquire_before_scanning(worker_engine, monkeypatch):
    from app.jobs import alert_worker

    stop = Event()
    sleeping = Event()
    wake = Event()
    current = [EARLY]
    observed = []
    real_scan = alert_worker.scan_once

    def checked_scan(sessions, now):
        with sessions() as session:
            pid, held = session.execute(select(
                func.pg_backend_pid(),
                text("EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=55120 AND objid=2 AND objsubid=2 AND granted)"),
            )).one()
            session.rollback()
        observed.append((pid, held))
        return real_scan(sessions, now)

    monkeypatch.setattr(alert_worker, "scan_once", checked_scan)

    def controlled_sleep(_seconds):
        sleeping.set()
        if current[0] == RISK_TIME:
            return
        wake.wait(timeout=3)
        wake.clear()

    worker = Thread(target=alert_worker.run_worker, args=(_sessions(worker_engine), 30, stop,
                                                          lambda: current[0], controlled_sleep), daemon=True)
    worker.start()
    try:
        assert sleeping.wait(timeout=3)
        assert len(observed) == 1 and observed[0][1] is True
        with worker_engine.connect() as other:
            assert other.exec_driver_sql("SELECT pg_terminate_backend(%s)", (observed[0][0],)).scalar_one()
            other.commit()
        current[0] = RISK_TIME
        wake.set()
        _eventually(lambda: len(observed) > 1 and len(_alerts(worker_engine)) == 1)
        assert all(held for _, held in observed)
        assert observed[1][0] != observed[0][0]
    finally:
        stop.set()
        wake.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
