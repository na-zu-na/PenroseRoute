from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.incidents.workflow import VehicleIncidentWorkflow
from app.schemas.common import ApiResponse
from app.schemas.incidents import (
    VehicleUnavailableRequest,
    VehicleUnavailableResponse,
)


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
