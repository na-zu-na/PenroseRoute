from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.resources.fleet import FleetService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.resources import (
    ResourceStatus,
    VehicleCreate,
    VehicleLocationUpdate,
    VehicleReplace,
    VehicleResponse,
    VehicleStatusUpdate,
)


router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("")
def list_vehicles(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    resource_status: Annotated[ResourceStatus | None, Query(alias="status")] = None,
) -> ApiResponse[PaginatedData[VehicleResponse]]:
    items, total = FleetService(db).list_vehicles(
        page=pagination.page,
        page_size=pagination.page_size,
        status=resource_status.value if resource_status is not None else None,
    )
    data = PaginatedData.from_items(
        items=[VehicleResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(data=data, message="Vehicles retrieved", request_id=request_id)


@router.get("/{vehicle_id}")
def get_vehicle(
    vehicle_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleResponse]:
    data = VehicleResponse.model_validate(FleetService(db).get_vehicle(vehicle_id))
    return success_response(data=data, message="Vehicle retrieved", request_id=request_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_vehicle(
    request: VehicleCreate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleResponse]:
    location = request.current_location
    vehicle = FleetService(db).create_vehicle(
        vehicle_code=request.vehicle_code,
        name=request.name,
        capacity_load_units=request.capacity_load_units,
        location_code=location.location_code,
        address_text=location.address_text,
        latitude=location.latitude,
        longitude=location.longitude,
        current_location_recorded_at=request.current_location_recorded_at,
    )
    return success_response(
        data=VehicleResponse.model_validate(vehicle),
        message="Vehicle created",
        request_id=request_id,
    )


@router.put("/{vehicle_id}")
def replace_vehicle(
    vehicle_id: UUID,
    request: VehicleReplace,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleResponse]:
    vehicle = FleetService(db).replace_vehicle(
        vehicle_id,
        name=request.name,
        capacity_load_units=request.capacity_load_units,
    )
    return success_response(
        data=VehicleResponse.model_validate(vehicle),
        message="Vehicle updated",
        request_id=request_id,
    )


@router.patch("/{vehicle_id}/status")
def update_vehicle_status(
    vehicle_id: UUID,
    request: VehicleStatusUpdate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleResponse]:
    vehicle = FleetService(db).update_vehicle_status(
        vehicle_id,
        status=request.status.value,
        business_date=request.business_date,
    )
    return success_response(
        data=VehicleResponse.model_validate(vehicle),
        message="Vehicle status updated",
        request_id=request_id,
    )


@router.patch("/{vehicle_id}/location")
def update_vehicle_location(
    vehicle_id: UUID,
    request: VehicleLocationUpdate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleResponse]:
    location = request.location
    vehicle = FleetService(db).update_vehicle_location(
        vehicle_id,
        location_code=location.location_code,
        address_text=location.address_text,
        latitude=location.latitude,
        longitude=location.longitude,
        recorded_at=request.recorded_at,
    )
    return success_response(
        data=VehicleResponse.model_validate(vehicle),
        message="Vehicle location updated",
        request_id=request_id,
    )
