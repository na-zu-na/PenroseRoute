from uuid import uuid4

from app.db.models.recovery import ImpactType, ReplanningScope
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.modules.incidents.impact import assess_merchant_order, assess_vehicle_order
from app.modules.incidents.scope import (
    initial_merchant_delay_scope,
    initial_vehicle_unavailable_scope,
)


def test_vehicle_impact_classification_uses_execution_facts() -> None:
    order_id = uuid4()
    cases = (
        (
            OrderExecutionStatus.COMPLETED,
            ImpactType.COMPLETED_FROZEN,
            True,
            True,
            False,
            False,
        ),
        (
            OrderExecutionStatus.PICKED_UP,
            ImpactType.HANDOVER_REQUIRED,
            True,
            False,
            True,
            True,
        ),
        (
            OrderExecutionStatus.DELIVERING,
            ImpactType.HANDOVER_REQUIRED,
            True,
            False,
            True,
            True,
        ),
        (
            OrderExecutionStatus.PICKUP_IN_PROGRESS,
            ImpactType.PICKUP_REPLAN,
            False,
            False,
            True,
            False,
        ),
        (
            OrderExecutionStatus.PLANNED,
            ImpactType.PICKUP_REPLAN,
            False,
            False,
            True,
            False,
        ),
    )

    for status, impact_type, picked_up, completed, replanning, handover in cases:
        result = assess_vehicle_order(
            order_id=order_id,
            execution_status=status,
            risk_status=OrderRiskStatus.AT_RISK,
        )
        assert result.impact_type is impact_type
        assert result.was_picked_up is picked_up
        assert result.was_completed is completed
        assert result.requires_replanning is replanning
        assert result.handover_required is handover
        assert result.risk_status_snapshot is OrderRiskStatus.AT_RISK


def test_vehicle_unavailable_initial_scope_is_affected_route() -> None:
    assert initial_vehicle_unavailable_scope() is ReplanningScope.AFFECTED_ROUTE


def test_merchant_delay_impacts_only_not_picked_up_orders() -> None:
    order_id = uuid4()
    route_id = uuid4()
    direct = assess_merchant_order(
        order_id=order_id,
        route_id=route_id,
        execution_status=OrderExecutionStatus.PLANNED,
        risk_status=OrderRiskStatus.NORMAL,
        is_direct=True,
        requires_replanning=True,
    )
    assert direct is not None
    assert direct.impact_type is ImpactType.WAITING_TIME_UPDATE
    assert direct.requires_replanning is True

    picked_up = assess_merchant_order(
        order_id=order_id,
        route_id=route_id,
        execution_status=OrderExecutionStatus.PICKED_UP,
        risk_status=OrderRiskStatus.AT_RISK,
        is_direct=False,
        requires_replanning=True,
    )
    assert picked_up is None
    assert initial_merchant_delay_scope() is ReplanningScope.AFFECTED_ROUTE
