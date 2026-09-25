"""Deterministic incident impact classification rules."""

from dataclasses import dataclass
from uuid import UUID

from app.db.models.recovery import ImpactType
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus


@dataclass(frozen=True)
class VehicleOrderImpact:
    order_id: UUID
    execution_status_snapshot: OrderExecutionStatus
    risk_status_snapshot: OrderRiskStatus
    was_picked_up: bool
    was_completed: bool
    requires_replanning: bool
    handover_required: bool
    impact_type: ImpactType
    impact_reason: str


@dataclass(frozen=True)
class MerchantOrderImpact:
    order_id: UUID
    route_id: UUID
    execution_status_snapshot: OrderExecutionStatus
    risk_status_snapshot: OrderRiskStatus
    requires_replanning: bool
    impact_type: ImpactType
    impact_reason: str


def assess_vehicle_order(
    *,
    order_id: UUID,
    execution_status: OrderExecutionStatus,
    risk_status: OrderRiskStatus,
) -> VehicleOrderImpact:
    if execution_status is OrderExecutionStatus.COMPLETED:
        return VehicleOrderImpact(
            order_id=order_id,
            execution_status_snapshot=execution_status,
            risk_status_snapshot=risk_status,
            was_picked_up=True,
            was_completed=True,
            requires_replanning=False,
            handover_required=False,
            impact_type=ImpactType.COMPLETED_FROZEN,
            impact_reason="Completed order is frozen and excluded from replanning.",
        )
    if execution_status in (
        OrderExecutionStatus.PICKED_UP,
        OrderExecutionStatus.DELIVERING,
    ):
        return VehicleOrderImpact(
            order_id=order_id,
            execution_status_snapshot=execution_status,
            risk_status_snapshot=risk_status,
            was_picked_up=True,
            was_completed=False,
            requires_replanning=True,
            handover_required=True,
            impact_type=ImpactType.HANDOVER_REQUIRED,
            impact_reason=(
                "Order cargo is on the unavailable vehicle and requires handover."
            ),
        )
    return VehicleOrderImpact(
        order_id=order_id,
        execution_status_snapshot=execution_status,
        risk_status_snapshot=risk_status,
        was_picked_up=False,
        was_completed=False,
        requires_replanning=True,
        handover_required=False,
        impact_type=ImpactType.PICKUP_REPLAN,
        impact_reason="Order was not picked up and must replan from its merchant.",
    )


def assess_merchant_order(
    *,
    order_id: UUID,
    route_id: UUID,
    execution_status: OrderExecutionStatus,
    risk_status: OrderRiskStatus,
    is_direct: bool,
    requires_replanning: bool,
) -> MerchantOrderImpact | None:
    if execution_status in (
        OrderExecutionStatus.PICKED_UP,
        OrderExecutionStatus.DELIVERING,
        OrderExecutionStatus.COMPLETED,
    ):
        return None
    return MerchantOrderImpact(
        order_id=order_id,
        route_id=route_id,
        execution_status_snapshot=execution_status,
        risk_status_snapshot=risk_status,
        requires_replanning=requires_replanning,
        impact_type=(
            ImpactType.WAITING_TIME_UPDATE
            if is_direct
            else ImpactType.DOWNSTREAM_ROUTE_IMPACT
        ),
        impact_reason=(
            "Merchant ready time changed before this order was picked up."
            if is_direct
            else "Merchant delay propagates to this later order on the same route."
        ),
    )
