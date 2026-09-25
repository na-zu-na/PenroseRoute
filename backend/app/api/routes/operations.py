from datetime import date
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.orm import Session

from app.api.auth import Principal, authenticate_dispatch_user
from app.api.dependencies import get_db, get_request_id
from app.api.routes.recovery import require_operations_user
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


# Agent-facing compatibility endpoints. Normal planning remains exclusively in
# routes/planning.py and never enters the Agent tool surface.
agent_router = APIRouter(tags=["dispatch-support"])


class IncidentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_date: date
    incident_type: Literal["VEHICLE_UNAVAILABLE", "MERCHANT_DELAY"]
    vehicle_route_id: UUID | None = None
    incident_location_id: UUID | None = None
    merchant_id: UUID | None = None
    updated_ready_at: datetime | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.incident_type == "VEHICLE_UNAVAILABLE":
            if not self.vehicle_route_id or not self.incident_location_id or self.merchant_id or self.updated_ready_at:
                raise ValueError("Vehicle event requires route and confirmed location only")
        elif not self.merchant_id or not self.updated_ready_at or self.vehicle_route_id or self.incident_location_id:
            raise ValueError("Merchant event requires merchant and new ready time only")
        if self.updated_ready_at and self.updated_ready_at.utcoffset() is None:
            raise ValueError("Explicit timezone is required")
        return self


def sessions():
    from app.db.session import SessionLocal
    return SessionLocal


def get_queries():
    from app.modules.dispatch.queries import DispatchQueries
    return DispatchQueries(sessions())


def get_incident_service():
    from app.modules.incidents.service import IncidentService
    return IncidentService(sessions())


def response(data, code="SUCCESS"):
    return {"success": True, "code": code, "message": "操作完成", "data": data,
            "request_id": "req_" + uuid4().hex}


@agent_router.get("/operations")
def agent_operations(business_date: date, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.operations(business_date))


@agent_router.get("/resources/availability")
def resources(business_date: date, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.resources(business_date))


@agent_router.get("/recovery-plans/{recovery_id}")
def proposal(recovery_id: UUID, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.proposal(recovery_id))


@agent_router.get("/delivery-plans/compare")
def compare(base_plan_id: UUID, candidate_plan_id: UUID, principal=Depends(authenticate_dispatch_user), queries=Depends(get_queries)):
    return response(queries.compare(base_plan_id, candidate_plan_id))


@agent_router.post("/incidents", status_code=201)
def report(command: IncidentCommand, principal: Principal = Depends(require_operations_user), service=Depends(get_incident_service)):
    return response(service.report(command, principal.subject), "INCIDENT_REPORTED")
