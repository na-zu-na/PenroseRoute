from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.incidents.workflow import MerchantDelayWorkflow, VehicleIncidentWorkflow
from app.modules.incidents.queries import IncidentQueryService
from app.modules.recovery.queries import RecoveryQueryService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.incidents import (
    IncidentAffectedOrderResponse,
    IncidentDetailResponse,
    IncidentListItemResponse,
    MerchantDelayRequest,
    MerchantDelayResponse,
    VehicleUnavailableRequest,
    VehicleUnavailableResponse,
)
from app.schemas.recovery import RecoveryPlanDetailResponse


router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("")
def list_incidents(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    business_date: date | None = None,
    incident_type: Literal["VEHICLE_UNAVAILABLE", "MERCHANT_DELAY"] | None = None,
    status: Literal["DETECTED", "ASSESSING", "REPLANNING", "REVIEW", "RESOLVED"] | None = None,
) -> ApiResponse[PaginatedData[IncidentListItemResponse]]:
    items, total = IncidentQueryService(db).list_incidents(
        page=pagination.page, page_size=pagination.page_size,
        business_date=business_date, incident_type=incident_type, status=status,
    )
    return success_response(
        data=PaginatedData.from_items(
            items=[IncidentListItemResponse.model_validate(item) for item in items],
            page=pagination.page, page_size=pagination.page_size, total=total,
        ),
        message="Incidents retrieved", request_id=request_id,
    )


@router.get("/{incident_id}")
def get_incident(
    incident_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[IncidentDetailResponse]:
    return success_response(
        data=IncidentDetailResponse.model_validate(IncidentQueryService(db).get_detail(incident_id)),
        message="Incident retrieved", request_id=request_id,
    )


@router.get("/{incident_id}/affected-orders")
def list_affected_orders(
    incident_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[list[IncidentAffectedOrderResponse]]:
    items = IncidentQueryService(db).list_affected_orders(incident_id)
    return success_response(
        data=[IncidentAffectedOrderResponse.model_validate(item) for item in items],
        message="Incident affected-order snapshots retrieved", request_id=request_id,
    )


@router.get("/{incident_id}/recovery-plans")
def list_incident_recovery_plans(
    incident_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[list[RecoveryPlanDetailResponse]]:
    items = RecoveryQueryService(db).list_attempts(incident_id)
    return success_response(
        data=[RecoveryPlanDetailResponse.model_validate(item) for item in items],
        message="Recovery attempts retrieved", request_id=request_id,
    )


@router.post("/vehicle-unavailable", status_code=status.HTTP_201_CREATED)
def report_vehicle_unavailable(
    request: VehicleUnavailableRequest,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleUnavailableResponse]:
    location = request.location
    result = VehicleIncidentWorkflow(db).report_unavailable(
        business_date=request.business_date,
        vehicle_id=request.vehicle_id,
        detected_at=request.detected_at,
        location_code=location.location_code if location else None,
        address_text=location.address_text if location else None,
        latitude=location.latitude if location else None,
        longitude=location.longitude if location else None,
    )
    return success_response(
        data=VehicleUnavailableResponse.model_validate(result),
        code="VEHICLE_INCIDENT_CREATED",
        message="Vehicle unavailable incident assessed",
        request_id=request_id,
    )


@router.post("/merchant-delay", status_code=status.HTTP_201_CREATED)
def report_merchant_delay(
    request: MerchantDelayRequest,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[MerchantDelayResponse]:
    result = MerchantDelayWorkflow(db).assess_delay(
        business_date=request.business_date,
        merchant_id=request.merchant_id,
        updated_ready_at=request.updated_ready_at,
        detected_at=request.detected_at,
        explicit_incident=True,
    )
    return success_response(
        data=MerchantDelayResponse.model_validate(result),
        code="MERCHANT_DELAY_ASSESSED",
        message="Merchant delay assessed",
        request_id=request_id,
    )
