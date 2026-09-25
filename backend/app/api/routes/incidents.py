from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.api.routes.recovery import require_operations_user
from app.core.responses import success_response
from app.modules.incidents.workflow import MerchantDelayWorkflow, VehicleIncidentWorkflow
from app.modules.recovery.deterministic_workflow import RecoveryWorkflow
from app.schemas.common import ApiResponse
from app.schemas.incidents import (
    MerchantDelayRequest,
    MerchantDelayResponse,
    VehicleUnavailableRequest,
    VehicleUnavailableResponse,
)
from app.schemas.recovery import StartRecoveryRequest, StartRecoveryResponse


router = APIRouter(prefix="/incidents", tags=["incidents"])


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


@router.post(
    "/{incident_id}/deterministic-recovery",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operations_user)],
)
def start_recovery(
    incident_id: UUID,
    request: StartRecoveryRequest,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[StartRecoveryResponse]:
    del request
    result = RecoveryWorkflow(db).start(incident_id)
    code = (
        "RECOVERY_PENDING_REVIEW"
        if result.outcome == "PENDING_REVIEW"
        else "NO_FEASIBLE_RECOVERY"
    )
    message = (
        "A valid recovery candidate is ready for dispatcher review"
        if result.outcome == "PENDING_REVIEW"
        else "No feasible recovery was found; manual intervention is required"
    )
    return success_response(
        data=StartRecoveryResponse.model_validate(result),
        code=code,
        message=message,
        request_id=request_id,
    )
