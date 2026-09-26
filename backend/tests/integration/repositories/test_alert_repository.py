"""Alert repository checks against disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.database.test_p1_alert_migration import _runner


PLAN = UUID("80000000-0000-0000-0000-000000000001")
ORDER = UUID("40000000-0000-0000-0000-000000000001")
AT = datetime(2026, 9, 25, 10, tzinfo=timezone.utc)


@pytest.fixture
def alert_engine(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    engine = create_engine(p1_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _alert():
    from app.db.models.alerts import RiskAlert
    return RiskAlert(
        delivery_plan_id=PLAN, order_id=ORDER, business_date=date(2026, 9, 25),
        risk_type="DELIVERY_WINDOW", status="ACTIVE", evidence={"reason": "window"},
        detected_at=AT, last_evaluated_at=AT,
    )


def _change(alert_id):
    from app.db.models.alerts import RiskAlertChange
    return RiskAlertChange(
        alert_id=alert_id, change_type="CREATED", recorded_at=AT,
        evidence_snapshot={"reason": "window"},
    )


def test_repository_reads_active_alerts_and_paged_history(alert_engine):
    from app.db.repositories.alert_repository import AlertRepository

    with Session(alert_engine) as session:
        alert = _alert()
        session.add(alert)
        session.flush()
        repository = AlertRepository(session)
        assert repository.lock_active(PLAN, ORDER, "DELIVERY_WINDOW").id == alert.id
        assert [item.id for item in repository.list_active_for_business_date(date(2026, 9, 25))] == [alert.id]
        first = repository.append_change(_change(alert.id))
        session.commit()
        alert_id = alert.id

    with Session(alert_engine) as session:
        repository = AlertRepository(session)
        items, total = repository.list_alerts(date(2026, 9, 25), "ACTIVE", 1, 20)
        assert total == 1 and [item.id for item in items] == [alert_id]
        assert [item.change_id for item in repository.list_changes(0, 100)] == [first]
        assert repository.list_changes(first, 100) == []


def test_change_allocation_waits_for_prior_writer_commit(alert_engine):
    from app.db.repositories.alert_repository import AlertRepository

    with Session(alert_engine) as setup:
        alert = _alert()
        setup.add(alert)
        setup.commit()
        alert_id = alert.id

    started = Event()
    writer_pid = []
    with Session(alert_engine) as a, ThreadPoolExecutor(max_workers=1) as pool:
        first = AlertRepository(a).append_change(_change(alert_id))

        def second_writer():
            with Session(alert_engine) as b:
                writer_pid.append(b.scalar(text("SELECT pg_backend_pid()")))
                started.set()
                second = AlertRepository(b).append_change(_change(alert_id))
                b.commit()
                return second

        future = pool.submit(second_writer)
        assert started.wait(timeout=2)
        try:
            with Session(alert_engine) as reader:
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    waiting_on = reader.execute(text(
                        "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid=:pid"
                    ), {"pid": writer_pid[0]}).one()
                    if waiting_on == ("Lock", "advisory"):
                        break
                    sleep(0.01)
                assert waiting_on == ("Lock", "advisory")
                assert AlertRepository(reader).list_changes(0, 100) == []
            assert not future.done()
        finally:
            a.commit()
        second = future.result(timeout=3)

    with Session(alert_engine) as reader:
        repository = AlertRepository(reader)
        assert [item.change_id for item in repository.list_changes(0, 100)] == [first, second]
        assert [item.change_id for item in repository.list_changes(first, 100)] == [second]
    assert first < second


def test_rollback_leaves_gap_without_losing_later_change(alert_engine):
    from app.db.repositories.alert_repository import AlertRepository

    with Session(alert_engine) as setup:
        alert = _alert()
        setup.add(alert)
        setup.commit()
        alert_id = alert.id

    with Session(alert_engine) as session:
        rolled_back = AlertRepository(session).append_change(_change(alert_id))
        session.rollback()
    with Session(alert_engine) as session:
        committed = AlertRepository(session).append_change(_change(alert_id))
        session.commit()
    assert committed > rolled_back
    with Session(alert_engine) as session:
        assert [item.change_id for item in AlertRepository(session).list_changes(0, 10)] == [committed]
