"""Incident and Recovery read API contract against the seeded PostgreSQL database."""
from contextlib import contextmanager
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models import Order, RecoveryPlan
from app.db.models.planning import ValidationStatus
from app.db.models.recovery import RecoveryPlanStatus, ReplanningScope, SolverStatus
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.session import engine
from app.main import app


VEHICLE_INCIDENT = "a0000000-0000-0000-0000-000000000001"
MERCHANT_INCIDENT = "a0000000-0000-0000-0000-000000000002"
MERCHANT_ATTEMPT = "e0000000-0000-0000-0000-000000000002"
VEHICLE_ATTEMPT = "e0000000-0000-0000-0000-000000000001"
BASE_PLAN = "80000000-0000-0000-0000-000000000001"
CANDIDATE_PLAN = "80000000-0000-0000-0000-000000000002"


@contextmanager
def api_client():
    connection = engine.connect()
    outer = connection.begin()
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
        outer.rollback()
        connection.close()


def test_incident_list_filters_and_detail_summarizes_source_impact_and_attempts():
    with api_client() as (client, _):
        listed = client.get("/api/incidents", params={
            "business_date": "2026-09-25", "incident_type": "VEHICLE_UNAVAILABLE",
            "status": "REVIEW", "page": 1, "page_size": 1,
        })
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["total"] == 1
        assert listed.json()["data"]["items"][0]["id"] == VEHICLE_INCIDENT
        assert client.get("/api/incidents", params={"business_date": "2026-09-24"}).json()["data"]["total"] == 0

        detail = client.get(f"/api/incidents/{VEHICLE_INCIDENT}")
        assert detail.status_code == 200, detail.text
        item = detail.json()["data"]
        assert item["base_delivery_plan_id"] == BASE_PLAN
        assert item["base_plan_code"] == "PLAN-20260925-V1"
        assert item["vehicle_id"] == "50000000-0000-0000-0000-000000000001"
        assert item["vehicle_route_id"] == "90000000-0000-0000-0000-000000000001"
        assert item["status"] == "REVIEW"
        assert item["affected_order_count"] == 2
        assert item["handover_order_count"] == 1
        assert item["impact_summary"] == {"COMPLETED_FROZEN": 1, "HANDOVER_REQUIRED": 1}
        assert [attempt["attempt_no"] for attempt in item["recovery_attempts"]] == [1]
        assert item["recovery_attempts"][0]["candidate_delivery_plan_id"] == CANDIDATE_PLAN


def test_affected_orders_remain_detection_snapshots_after_current_order_changes():
    with api_client() as (client, session):
        order = session.get(Order, UUID("40000000-0000-0000-0000-000000000002"))
        order.execution_status = OrderExecutionStatus.COMPLETED
        order.risk_status = OrderRiskStatus.NORMAL
        session.flush()
        response = client.get(f"/api/incidents/{VEHICLE_INCIDENT}/affected-orders")
        assert response.status_code == 200, response.text
        rows = {row["order_id"]: row for row in response.json()["data"]}
        snapshot = rows[str(order.id)]
        assert snapshot["execution_status_snapshot"] == "PICKED_UP"
        assert snapshot["risk_status_snapshot"] == "AT_RISK"
        assert snapshot["was_picked_up"] is True
        assert snapshot["was_completed"] is False
        assert snapshot["handover_required"] is True
        assert snapshot["requires_replanning"] is True
        assert snapshot["impact_type"] == "HANDOVER_REQUIRED"
        assert snapshot["original_vehicle_route_id"] == "90000000-0000-0000-0000-000000000001"


def test_recovery_queries_include_ordered_infeasible_and_error_without_candidates():
    with api_client() as (client, session):
        first = session.get(RecoveryPlan, UUID(MERCHANT_ATTEMPT))
        first.solver_status = SolverStatus.INFEASIBLE
        second = RecoveryPlan(
            id=uuid4(), recovery_code=f"TEST-REC-{uuid4().hex[:8]}",
            incident_id=UUID(MERCHANT_INCIDENT), attempt_no=2,
            previous_recovery_plan_id=first.id,
            base_delivery_plan_id=UUID(BASE_PLAN),
            candidate_delivery_plan_id=None,
            status=RecoveryPlanStatus.DRAFT, replanning_scope=ReplanningScope.CROSS_ROUTE,
            scope_description="Second scope", solver_status=SolverStatus.ERROR,
            validation_status=None, agent_explanation=None,
        )
        session.add(second)
        session.flush()

        response = client.get(f"/api/incidents/{MERCHANT_INCIDENT}/recovery-plans")
        assert response.status_code == 200, response.text
        attempts = response.json()["data"]
        assert [row["attempt_no"] for row in attempts] == [1, 2]
        assert [row["solver_status"] for row in attempts] == ["INFEASIBLE", "ERROR"]
        assert all(row["candidate_delivery_plan_id"] is None for row in attempts)
        assert all(row["validation_status"] is None for row in attempts)
        assert all(row["agent_explanation"] is None for row in attempts)

        detail = client.get(f"/api/recovery-plans/{second.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["base_delivery_plan_id"] == BASE_PLAN
        assert detail.json()["data"]["candidate_delivery_plan_id"] is None
        assert detail.json()["data"]["previous_recovery_plan_id"] == MERCHANT_ATTEMPT


def test_invalid_attempt_and_valid_candidate_details_preserve_nullability():
    with api_client() as (client, session):
        invalid = session.get(RecoveryPlan, UUID(MERCHANT_ATTEMPT))
        invalid.solver_status = SolverStatus.FEASIBLE
        invalid.validation_status = ValidationStatus.INVALID
        session.flush()
        failed = client.get(f"/api/recovery-plans/{MERCHANT_ATTEMPT}")
        assert failed.status_code == 200, failed.text
        assert failed.json()["data"]["validation_status"] == "INVALID"
        assert failed.json()["data"]["candidate_delivery_plan_id"] is None
        assert failed.json()["data"]["agent_explanation"] is None

        valid = client.get(f"/api/recovery-plans/{VEHICLE_ATTEMPT}")
        assert valid.status_code == 200
        assert valid.json()["data"]["candidate_delivery_plan_id"] == CANDIDATE_PLAN
        assert valid.json()["data"]["solver_status"] == "FEASIBLE"
        assert valid.json()["data"]["validation_status"] == "VALID"


def test_incident_and_recovery_queries_return_not_found():
    missing = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    with api_client() as (client, _):
        for path, code in (
            (f"/api/incidents/{missing}", "INCIDENT_NOT_FOUND"),
            (f"/api/incidents/{missing}/affected-orders", "INCIDENT_NOT_FOUND"),
            (f"/api/incidents/{missing}/recovery-plans", "INCIDENT_NOT_FOUND"),
            (f"/api/recovery-plans/{missing}", "RECOVERY_NOT_FOUND"),
        ):
            response = client.get(path)
            assert response.status_code == 404, (path, response.text)
            assert response.json()["code"] == code
