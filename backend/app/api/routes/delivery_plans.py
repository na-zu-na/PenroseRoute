from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
)
from app.modules.planning.queries import PlanningQueryService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.planning import (
    DeliveryPlanResponse,
    PlanOrderMembershipResponse,
    VehicleRouteResponse,
)


router = APIRouter(prefix="/delivery-plans", tags=["delivery-plans"])


@router.get("")
def list_delivery_plans(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    business_date: date | None = None,
    status: DeliveryPlanStatus | None = None,
    plan_group_id: UUID | None = None,
) -> ApiResponse[PaginatedData[DeliveryPlanResponse]]:
    items, total = PlanningQueryService(db).list_plans(
        page=pagination.page,
        page_size=pagination.page_size,
        business_date=business_date,
        status=status,
        plan_group_id=plan_group_id,
    )
    data = PaginatedData.from_items(
        items=[DeliveryPlanResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(
        data=data,
        message="Delivery plans retrieved",
        request_id=request_id,
    )


@router.get("/current")
def get_current_delivery_plan(
    business_date: date,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DeliveryPlanResponse]:
    data = DeliveryPlanResponse.model_validate(
        PlanningQueryService(db).get_current_plan(business_date)
    )
    return success_response(
        data=data,
        message="Current delivery plan retrieved",
        request_id=request_id,
    )


@router.get("/{plan_id}")
def get_delivery_plan(
    plan_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DeliveryPlanResponse]:
    data = DeliveryPlanResponse.model_validate(
        PlanningQueryService(db).get_plan(plan_id)
    )
    return success_response(
        data=data,
        message="Delivery plan retrieved",
        request_id=request_id,
    )


@router.get("/{plan_id}/orders")
def list_delivery_plan_orders(
    plan_id: UUID,
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    assignment_status: PlanOrderAssignmentStatus | None = None,
) -> ApiResponse[PaginatedData[PlanOrderMembershipResponse]]:
    items, total = PlanningQueryService(db).list_plan_orders(
        plan_id,
        page=pagination.page,
        page_size=pagination.page_size,
        assignment_status=assignment_status,
    )
    data = PaginatedData.from_items(
        items=[PlanOrderMembershipResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(
        data=data,
        message="Delivery plan orders retrieved",
        request_id=request_id,
    )


@router.get("/{plan_id}/routes")
def list_delivery_plan_routes(
    plan_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[list[VehicleRouteResponse]]:
    routes = PlanningQueryService(db).list_plan_routes(plan_id)
    data = [VehicleRouteResponse.model_validate(route) for route in routes]
    return success_response(
        data=data,
        message="Delivery plan routes retrieved",
        request_id=request_id,
    )
