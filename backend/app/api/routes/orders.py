from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.resources.orders import OrderService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.resources import (
    CurrentOrderPlanResponse, OrderCreate, OrderExecutionStatus,
    OrderExecutionUpdate, OrderReplace, OrderResponse, OrderRiskStatus,
)


router = APIRouter(prefix="/orders", tags=["orders"])


def _detail(service: OrderService, order_id: UUID) -> OrderResponse:
    order = service.get_order(order_id)
    response = OrderResponse.model_validate(order)
    membership = service.current_plan(order_id)
    if membership is None:
        return response
    plan_order, plan = membership
    return response.model_copy(update={"current_plan": CurrentOrderPlanResponse(
        delivery_plan_id=plan.id,
        plan_code=plan.plan_code,
        assignment_status=plan_order.assignment_status.value,
        vehicle_route_id=plan_order.vehicle_route_id,
        route_no=plan_order.vehicle_route.route_no if plan_order.vehicle_route else None,
        vehicle_id=plan_order.vehicle_route.vehicle_id if plan_order.vehicle_route else None,
        driver_id=plan_order.vehicle_route.driver_id if plan_order.vehicle_route else None,
    )})


@router.get("")
def list_orders(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    business_date: date | None = None,
    execution_status: OrderExecutionStatus | None = None,
    risk_status: OrderRiskStatus | None = None,
    merchant_id: UUID | None = None,
) -> ApiResponse[PaginatedData[OrderResponse]]:
    items, total = OrderService(db).list_orders(
        page=pagination.page, page_size=pagination.page_size, business_date=business_date,
        execution_status=execution_status.value if execution_status else None,
        risk_status=risk_status.value if risk_status else None, merchant_id=merchant_id,
    )
    return success_response(
        data=PaginatedData.from_items(
            items=[OrderResponse.model_validate(item) for item in items],
            page=pagination.page, page_size=pagination.page_size, total=total,
        ),
        message="Orders retrieved", request_id=request_id,
    )


@router.get("/{order_id}")
def get_order(
    order_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[OrderResponse]:
    return success_response(
        data=_detail(OrderService(db), order_id),
        message="Order retrieved", request_id=request_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(
    request: OrderCreate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[OrderResponse]:
    order = OrderService(db).create_order(request.model_dump())
    return success_response(
        data=OrderResponse.model_validate(order),
        message="Order created", request_id=request_id,
    )


@router.put("/{order_id}")
def replace_order(
    order_id: UUID,
    request: OrderReplace,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[OrderResponse]:
    order = OrderService(db).replace_order(order_id, request.model_dump())
    return success_response(
        data=OrderResponse.model_validate(order),
        message="Order updated", request_id=request_id,
    )


@router.patch("/{order_id}/execution-status")
def advance_execution(
    order_id: UUID,
    request: OrderExecutionUpdate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[OrderResponse]:
    order = OrderService(db).advance_execution(order_id, request.status.value)
    return success_response(
        data=OrderResponse.model_validate(order),
        message="Order execution status updated", request_id=request_id,
    )
