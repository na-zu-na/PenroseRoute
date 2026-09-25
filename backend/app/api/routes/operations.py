from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.modules.operations.queries import OperationsQueryService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.operations import (
    OperationOrderResponse,
    OperationRouteResponse,
    OperationsDashboardResponse,
    OperationVehicleResponse,
)


router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/dashboard")
def get_operations_dashboard(
    business_date: date,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[OperationsDashboardResponse]:
    data = OperationsDashboardResponse.model_validate(
        OperationsQueryService(db).dashboard(business_date)
    )
    return success_response(
        data=data,
        message="Operations dashboard retrieved",
        request_id=request_id,
    )


@router.get("/orders")
def list_operation_orders(
    business_date: date,
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    execution_status: OrderExecutionStatus | None = None,
    risk_status: OrderRiskStatus | None = None,
) -> ApiResponse[PaginatedData[OperationOrderResponse]]:
    items, total = OperationsQueryService(db).list_orders(
        business_date,
        page=pagination.page,
        page_size=pagination.page_size,
        execution_status=execution_status,
        risk_status=risk_status,
    )
    data = PaginatedData.from_items(
        items=[OperationOrderResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(
        data=data, message="Operation orders retrieved", request_id=request_id
    )


@router.get("/vehicles")
def list_operation_vehicles(
    business_date: date,
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[PaginatedData[OperationVehicleResponse]]:
    items, total = OperationsQueryService(db).list_vehicles(
        business_date, page=pagination.page, page_size=pagination.page_size
    )
    data = PaginatedData.from_items(
        items=[OperationVehicleResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(
        data=data, message="Operation vehicles retrieved", request_id=request_id
    )


@router.get("/routes")
def list_operation_routes(
    business_date: date,
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[PaginatedData[OperationRouteResponse]]:
    items, total = OperationsQueryService(db).list_routes(
        business_date, page=pagination.page, page_size=pagination.page_size
    )
    data = PaginatedData.from_items(
        items=[OperationRouteResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(
        data=data, message="Operation routes retrieved", request_id=request_id
    )
