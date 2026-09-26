"""Formal Recovery comparison API; all writes are rolled back."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core.errors import Conflict
from app.db.models import DeliveryPlan, RouteStop
from app.db.models.planning import DeliveryPlanStatus
from app.db.session import engine
from app.main import app
from app.modules.decisions.service import DeterministicDecisionService


ATTEMPT = "e0000000-0000-0000-0000-000000000001"
FAILED_ATTEMPT = "e0000000-0000-0000-0000-000000000002"
BASE = UUID("80000000-0000-0000-0000-000000000001")
CANDIDATE = UUID("80000000-0000-0000-0000-000000000002")


@contextmanager
def api_client():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, autoflush=False, expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app), session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        transaction.rollback()
        connection.close()


def test_pending_candidate_comparison_is_read_only_and_uses_response_envelope():
    with api_client() as (client, session):
        response = client.get(
            f"/api/recovery-plans/{ATTEMPT}/comparison",
            headers={"X-Request-ID": "p1-compare-test"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["code"] == "SUCCESS"
        assert body["request_id"] == "p1-compare-test"
        data = body["data"]
        assert data["recovery_plan_id"] == ATTEMPT
        assert data["base_plan_id"] == str(BASE)
        assert data["candidate_plan_id"] == str(CANDIDATE)
        assert data["reviewable"] is True
        assert data["remaining_metrics"]["base_distance_meters"] is None
        assert data["remaining_metrics"]["reason"] == "NO_COMPARABLE_REMAINDER_SNAPSHOT"
        assert any(change["stop_type"] == "HANDOVER" and change["change_type"] == "ADDED" for change in data["stop_changes"])
        assert session.get(DeliveryPlan, BASE).status == DeliveryPlanStatus.CURRENT
        assert session.get(DeliveryPlan, CANDIDATE).status == DeliveryPlanStatus.CANDIDATE


def test_stale_base_comparison_does_not_promise_approval():
    with api_client() as (client, session):
        first = client.get(f"/api/recovery-plans/{ATTEMPT}/comparison")
        assert first.status_code == 200
        assert first.json()["data"]["reviewable"] is True
        base = session.get(DeliveryPlan, BASE)
        base.status = DeliveryPlanStatus.SUPERSEDED
        base.superseded_at = datetime.now(timezone.utc)
        session.flush()
        second = client.get(f"/api/recovery-plans/{ATTEMPT}/comparison")
        assert second.status_code == 200
        assert second.json()["data"]["reviewable"] is False
        with pytest.raises(Conflict) as rejection:
            DeterministicDecisionService._lock_reviewable(session, UUID(ATTEMPT))
        assert rejection.value.code == "BASE_PLAN_NOT_CURRENT"


def test_failed_attempt_missing_attempt_and_invalid_id_have_distinct_errors():
    with api_client() as (client, _):
        cases = (
            (FAILED_ATTEMPT, 404, "CANDIDATE_PLAN_NOT_FOUND"),
            ("ffffffff-ffff-ffff-ffff-ffffffffffff", 404, "RECOVERY_NOT_FOUND"),
            ("not-a-uuid", 422, "VALIDATION_ERROR"),
        )
        for attempt_id, status, code in cases:
            response = client.get(f"/api/recovery-plans/{attempt_id}/comparison")
            assert response.status_code == status, response.text
            assert response.json()["code"] == code


def test_invalid_candidate_relationship_returns_business_validation_error():
    with api_client() as (client, session):
        candidate = session.get(DeliveryPlan, CANDIDATE)
        candidate.plan_group_id = UUID(int=123)
        session.flush()
        response = client.get(f"/api/recovery-plans/{ATTEMPT}/comparison")
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "PLAN_COMPARISON_INVALID"


def test_changed_stop_time_is_serialized_with_before_and_after_values():
    with api_client() as (client, session):
        stop = session.get(RouteStop, UUID("c0000000-0000-0000-0000-000000000015"))
        before = stop.planned_arrival_at
        after = before + timedelta(minutes=1)
        stop.planned_arrival_at = after
        session.flush()

        response = client.get(f"/api/recovery-plans/{ATTEMPT}/comparison")
        assert response.status_code == 200, response.text
        changes = response.json()["data"]["stop_changes"]
        pickup = next(item for item in changes if item["order_id"] == "40000000-0000-0000-0000-000000000003" and item["stop_type"] == "PICKUP")
        assert datetime.fromisoformat(pickup["candidate_planned_arrival_at"]) == after
        assert datetime.fromisoformat(pickup["base_planned_arrival_at"]).hour == 9
