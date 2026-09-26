"""Read-only comparison against existing PostgreSQL seed data."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.core.errors import BusinessError, NotFound
from app.db.models import DeliveryPlan, RecoveryPlan, RouteStop
from app.db.models.planning import DeliveryPlanStatus
from app.db.session import engine


ATTEMPT_ID = UUID("e0000000-0000-0000-0000-000000000001")
FAILED_ATTEMPT_ID = UUID("e0000000-0000-0000-0000-000000000002")
BASE_ID = UUID("80000000-0000-0000-0000-000000000001")
CANDIDATE_ID = UUID("80000000-0000-0000-0000-000000000002")
ORDER_PICKED_UP = UUID("40000000-0000-0000-0000-000000000002")
ORDER_COMPLETED = UUID("40000000-0000-0000-0000-000000000001")
ORDER_UNASSIGNED = UUID("40000000-0000-0000-0000-000000000006")


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_seeded_recovery_reports_handover_reassignment_and_frozen_work(db_session):
    from app.modules.planning.comparison import PlanComparisonService

    result = PlanComparisonService(db_session).compare_recovery(ATTEMPT_ID)

    assert result.base_plan_id == BASE_ID
    assert result.candidate_plan_id == CANDIDATE_ID
    assert result.reviewable is True
    assert result.comparison_time_basis == "ATTEMPT_RECORDED_AT"
    assert result.frozen_completed_order_ids == (ORDER_COMPLETED,)
    changed = {item.order_id: item for item in result.orders}
    assert changed[ORDER_PICKED_UP].assignment_changed is True
    assert changed[ORDER_PICKED_UP].candidate_vehicle_id == UUID("50000000-0000-0000-0000-000000000003")
    assert changed[ORDER_PICKED_UP].eta_basis == "PLANNED_DELIVERY_ARRIVAL"
    assert changed[ORDER_UNASSIGNED].base_unassigned_reason_code == "CAPACITY_INFEASIBLE"
    assert changed[ORDER_UNASSIGNED].candidate_unassigned_reason_code == "NO_FEASIBLE_ROUTE"
    assert changed[ORDER_UNASSIGNED].candidate_unassigned_reason_detail == "Order remains unassigned after recovery optimization."
    assert any(item.order_id == ORDER_PICKED_UP and item.stop_type == "HANDOVER" and item.change_type == "ADDED" for item in result.stop_changes)
    assert all(item.stop_type != "PICKUP" or item.order_id != ORDER_PICKED_UP or item.change_type != "ADDED" for item in result.stop_changes)
    assert result.remaining_metrics.base_distance_meters is None
    assert db_session.get(DeliveryPlan, BASE_ID).status == DeliveryPlanStatus.CURRENT


def test_missing_attempt_and_candidate_have_distinct_errors(db_session):
    from app.modules.planning.comparison import PlanComparisonService

    service = PlanComparisonService(db_session)
    with pytest.raises(NotFound) as missing:
        service.compare_recovery(UUID(int=0))
    assert missing.value.code == "RECOVERY_NOT_FOUND"
    with pytest.raises(NotFound) as failed:
        service.compare_recovery(FAILED_ATTEMPT_ID)
    assert failed.value.code == "CANDIDATE_PLAN_NOT_FOUND"


def test_unrelated_candidate_and_mismatched_business_date_are_rejected(db_session):
    from app.modules.planning.comparison import PlanComparisonService

    attempt = db_session.get(RecoveryPlan, ATTEMPT_ID)
    candidate = db_session.get(DeliveryPlan, CANDIDATE_ID)
    candidate.plan_group_id = UUID(int=123)
    db_session.flush()
    with pytest.raises(BusinessError) as invalid:
        PlanComparisonService(db_session).compare_recovery(attempt.id)
    assert invalid.value.code == "PLAN_COMPARISON_INVALID"
    candidate.plan_group_id = db_session.get(DeliveryPlan, BASE_ID).plan_group_id
    candidate.business_date = datetime(2026, 9, 26, tzinfo=timezone.utc).date()
    db_session.flush()
    with pytest.raises(BusinessError) as invalid_date:
        PlanComparisonService(db_session).compare_recovery(attempt.id)
    assert invalid_date.value.code == "PLAN_COMPARISON_INVALID"


def test_candidate_with_wrong_parent_plan_is_rejected(db_session):
    from app.modules.planning.comparison import PlanComparisonService

    candidate = db_session.get(DeliveryPlan, CANDIDATE_ID)
    candidate.parent_plan_id = candidate.id
    db_session.flush()
    with pytest.raises(BusinessError) as invalid:
        PlanComparisonService(db_session).compare_recovery(ATTEMPT_ID)
    assert invalid.value.code == "PLAN_COMPARISON_INVALID"


def test_nonpending_attempt_and_cancelled_candidate_are_not_reviewable(db_session):
    from app.modules.planning.comparison import PlanComparisonService
    from app.db.models.recovery import RecoveryPlanStatus

    attempt = db_session.get(RecoveryPlan, ATTEMPT_ID)
    candidate = db_session.get(DeliveryPlan, CANDIDATE_ID)
    attempt.status = RecoveryPlanStatus.DRAFT
    db_session.flush()
    assert PlanComparisonService(db_session).compare_recovery(ATTEMPT_ID).reviewable is False
    attempt.status = RecoveryPlanStatus.PENDING_REVIEW
    candidate.status = DeliveryPlanStatus.CANCELLED
    db_session.flush()
    assert PlanComparisonService(db_session).compare_recovery(ATTEMPT_ID).reviewable is False


def test_stale_base_is_comparable_but_not_reviewable(db_session):
    from app.modules.planning.comparison import PlanComparisonService

    base = db_session.get(DeliveryPlan, BASE_ID)
    base.status = DeliveryPlanStatus.SUPERSEDED
    base.superseded_at = datetime.now(timezone.utc)
    db_session.flush()
    result = PlanComparisonService(db_session).compare_recovery(ATTEMPT_ID)
    assert result.reviewable is False
    assert result.base_plan_status == "SUPERSEDED"


def test_absent_delivery_stop_yields_missing_eta_reason(db_session):
    from app.modules.planning.comparison import PlanComparisonService

    stop = db_session.get(RouteStop, UUID("c0000000-0000-0000-0000-000000000014"))
    db_session.delete(stop)
    db_session.flush()
    result = PlanComparisonService(db_session).compare_recovery(ATTEMPT_ID)
    order = next(item for item in result.orders if item.order_id == ORDER_PICKED_UP)
    assert order.eta_delta_seconds is None
    assert order.eta_unavailable_reason == "DELIVERY_STOP_NOT_AVAILABLE"
