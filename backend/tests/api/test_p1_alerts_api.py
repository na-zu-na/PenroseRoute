"""Formal read-only P1 alert APIs against disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Event
from time import monotonic, sleep
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models.alerts import AlertStatus, RiskAlert, RiskAlertChange
from app.db.repositories.alert_repository import AlertRepository
from app.main import app
from app.modules.operations.alerts import AlertService
from tests.database.test_p1_alert_migration import _runner


DAY = date(2026, 9, 25)
AT = datetime(2026, 9, 25, 10, 5, tzinfo=timezone(timedelta(hours=8)))
PLAN = UUID("80000000-0000-0000-0000-000000000001")
ROUTE = UUID("90000000-0000-0000-0000-000000000002")
ORDER_3 = UUID("40000000-0000-0000-0000-000000000003")
ORDER_4 = UUID("40000000-0000-0000-0000-000000000004")
ORDER_5 = UUID("40000000-0000-0000-0000-000000000005")


@pytest.fixture
def alert_api(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    engine = create_engine(p1_database_url)

    def override_get_db():
        with Session(engine, autoflush=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def _alert(order_id, *, status="ACTIVE", detected_at=AT):
    return RiskAlert(
        delivery_plan_id=PLAN, order_id=order_id, business_date=DAY,
        risk_type="DELIVERY_WINDOW", status=status,
        evidence={
            "reason_category": "APPROACHING_WINDOW" if status == "ACTIVE" else "RISK_CLEARED",
            "vehicle_route_id": str(ROUTE),
            "estimated_arrival_at": AT.isoformat(),
            "delivery_window_end_at": (AT + timedelta(minutes=10)).isoformat(),
            "threshold_seconds": 900,
        },
        detected_at=detected_at,
        last_evaluated_at=detected_at if status == "ACTIVE" else detected_at + timedelta(minutes=1),
        resolved_at=None if status == "ACTIVE" else detected_at + timedelta(minutes=1),
    )


def _change(alert_id, kind, evidence, at=AT):
    return RiskAlertChange(
        alert_id=alert_id, change_type=kind, recorded_at=at,
        evidence_snapshot=dict(evidence),
    )


def _seed_three_alerts(engine):
    with Session(engine) as session:
        active_a = _alert(ORDER_3)
        active_b = _alert(ORDER_5, detected_at=AT + timedelta(seconds=1))
        resolved = _alert(ORDER_4, status="RESOLVED")
        session.add_all([active_a, active_b, resolved])
        session.flush()
        repo = AlertRepository(session)
        for alert in (active_a, active_b):
            repo.append_change(_change(alert.id, "CREATED", alert.evidence))
        repo.append_change(_change(resolved.id, "CREATED", {
            "reason_category": "APPROACHING_WINDOW", "vehicle_route_id": str(ROUTE),
        }))
        repo.append_change(_change(resolved.id, "RESOLVED", resolved.evidence))
        ids = (active_a.id, active_b.id, resolved.id)
        session.commit()
    return ids


def test_alert_list_supports_active_history_date_and_pagination(alert_api):
    client, engine = alert_api
    active_a, active_b, resolved = _seed_three_alerts(engine)

    first = client.get("/api/operations/alerts", params={
        "business_date": str(DAY), "status": "ACTIVE", "page": 1, "page_size": 1,
    }, headers={"X-Request-ID": "p1-alert-list"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["success"], body["code"], body["request_id"]) == (True, "SUCCESS", "p1-alert-list")
    assert body["data"]["total"] == 2
    assert body["data"]["total_pages"] == 2
    assert body["data"]["items"][0]["id"] in (str(active_a), str(active_b))
    item = body["data"]["items"][0]
    assert item["delivery_plan_id"] == str(PLAN)
    assert item["vehicle_route_id"] == str(ROUTE)
    assert item["order_id"] in (str(ORDER_3), str(ORDER_5))
    assert item["reason_category"] == "APPROACHING_WINDOW"
    assert item["status"] == "ACTIVE"
    assert item["evidence"]["threshold_seconds"] == 900

    second = client.get("/api/operations/alerts", params={
        "business_date": str(DAY), "status": "ACTIVE", "page": 2, "page_size": 1,
    })
    assert second.status_code == 200
    assert {item["id"] for item in (first.json()["data"]["items"][0], second.json()["data"]["items"][0])} == {
        str(active_a), str(active_b),
    }
    history = client.get("/api/operations/alerts", params={"business_date": str(DAY), "status": "RESOLVED"})
    assert history.status_code == 200
    assert history.json()["data"]["total"] == 1
    assert history.json()["data"]["items"][0]["id"] == str(resolved)
    assert history.json()["data"]["items"][0]["resolved_at"] is not None
    other_day = client.get("/api/operations/alerts", params={"business_date": "2030-01-01"})
    assert other_day.status_code == 200
    assert other_day.json()["data"]["items"] == []


def test_change_cursor_keeps_same_timestamp_events_and_empty_page_position(alert_api):
    client, engine = alert_api
    _seed_three_alerts(engine)
    cursor = 0
    types = []
    ids = []
    for _ in range(4):
        response = client.get("/api/operations/alerts/changes", params={"after": cursor, "limit": 1})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == "SUCCESS"
        item = body["data"]["items"][0]
        ids.append(item["change_id"])
        types.append(item["change_type"])
        assert item["delivery_plan_id"] == str(PLAN)
        assert item["vehicle_route_id"] == str(ROUTE)
        assert item["order_id"] in (str(ORDER_3), str(ORDER_4), str(ORDER_5))
        assert item["reason_category"] is not None
        cursor = body["data"]["next_cursor"]
        assert cursor == item["change_id"]
    assert ids == sorted(ids) and len(set(ids)) == 4
    assert types == ["CREATED", "CREATED", "CREATED", "RESOLVED"]
    empty = client.get("/api/operations/alerts/changes", params={"after": cursor, "limit": 1})
    assert empty.status_code == 200
    assert empty.json()["data"] == {"items": [], "next_cursor": cursor}
    untouched = client.get("/api/operations/alerts/changes", params={"after": 999999999, "limit": 1})
    assert untouched.json()["data"]["next_cursor"] == 999999999


@pytest.mark.parametrize("path,params", [
    ("/api/operations/alerts", {"status": "UNKNOWN"}),
    ("/api/operations/alerts", {"page": 0}),
    ("/api/operations/alerts", {"page_size": 101}),
    ("/api/operations/alerts", {"business_date": "not-a-date"}),
    ("/api/operations/alerts/changes", {"after": -1}),
    ("/api/operations/alerts/changes", {"after": 9223372036854775808}),
    ("/api/operations/alerts/changes", {"limit": 0}),
    ("/api/operations/alerts/changes", {"limit": 101}),
])
def test_alert_query_rejects_invalid_parameters(alert_api, path, params):
    client, _ = alert_api
    response = client.get(path, params=params)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_alert_gets_do_not_recalculate_or_write_business_facts(alert_api, monkeypatch):
    client, engine = alert_api
    _seed_three_alerts(engine)
    with Session(engine) as session:
        before = (
            session.scalar(select(func.count()).select_from(RiskAlert)),
            session.scalar(select(func.count()).select_from(RiskAlertChange)),
            session.scalar(select(func.max(RiskAlert.last_evaluated_at))),
        )

    def must_not_evaluate(*_args, **_kwargs):
        raise AssertionError("GET must not evaluate risk")

    monkeypatch.setattr(AlertService, "evaluate_business_date", must_not_evaluate)
    assert client.get("/api/operations/alerts", params={"business_date": str(DAY)}).status_code == 200
    assert client.get("/api/operations/alerts/changes", params={"after": 0}).status_code == 200
    with Session(engine) as session:
        after = (
            session.scalar(select(func.count()).select_from(RiskAlert)),
            session.scalar(select(func.count()).select_from(RiskAlertChange)),
            session.scalar(select(func.max(RiskAlert.last_evaluated_at))),
        )
    assert after == before


def test_missing_historical_evidence_is_not_invented(alert_api):
    client, engine = alert_api
    with Session(engine) as session:
        alert = _alert(ORDER_3)
        alert.evidence = {}
        session.add(alert)
        session.flush()
        AlertRepository(session).append_change(_change(alert.id, "CREATED", {}))
        session.commit()

    item = client.get("/api/operations/alerts").json()["data"]["items"][0]
    change = client.get("/api/operations/alerts/changes").json()["data"]["items"][0]
    assert item["reason_category"] is None and item["vehicle_route_id"] is None
    assert change["reason_category"] is None and change["vehicle_route_id"] is None


def test_concurrent_writer_commit_order_cannot_skip_change_cursor(alert_api):
    client, engine = alert_api
    active_a, _, _ = _seed_three_alerts(engine)
    with Session(engine) as session:
        alert = session.get(RiskAlert, active_a)
        initial = AlertRepository(session).list_changes(0, 1)[0].change_id
        evidence = dict(alert.evidence)

    started = Event()
    writer_pid = []
    with Session(engine) as a, ThreadPoolExecutor(max_workers=1) as pool:
        first = AlertRepository(a).append_change(_change(active_a, "UPDATED", evidence))

        def second_writer():
            with Session(engine) as b:
                writer_pid.append(b.scalar(text("SELECT pg_backend_pid()")))
                started.set()
                second = AlertRepository(b).append_change(_change(active_a, "UPDATED", evidence))
                b.commit()
                return second

        future = pool.submit(second_writer)
        assert started.wait(timeout=2)
        try:
            with Session(engine) as observer:
                deadline = monotonic() + 3
                while monotonic() < deadline:
                    waiting = observer.execute(text(
                        "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid=:pid"
                    ), {"pid": writer_pid[0]}).one()
                    if waiting == ("Lock", "advisory"):
                        break
                    sleep(0.01)
                assert waiting == ("Lock", "advisory")
            visible = client.get("/api/operations/alerts/changes", params={"after": first - 1})
            assert visible.status_code == 200
            assert visible.json()["data"]["items"] == []
            assert visible.json()["data"]["next_cursor"] == first - 1
            assert not future.done()
        finally:
            a.commit()
        second = future.result(timeout=3)

    page_one = client.get("/api/operations/alerts/changes", params={"after": initial, "limit": 1}).json()["data"]
    page_two = client.get("/api/operations/alerts/changes", params={"after": page_one["next_cursor"], "limit": 1}).json()["data"]
    # Other seeded changes may lie between `initial` and this pair; the committed stream remains monotonic.
    all_changes = client.get("/api/operations/alerts/changes", params={"after": initial}).json()["data"]["items"]
    cursors = [item["change_id"] for item in all_changes]
    assert first < second
    assert first in cursors and second in cursors
    assert cursors == sorted(cursors)
    assert page_one["next_cursor"] == page_one["items"][0]["change_id"]
    assert page_two["next_cursor"] == page_two["items"][0]["change_id"]
