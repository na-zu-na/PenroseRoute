from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.resources.fleet import FleetService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.resources import (
    DriverCreate,
    DriverReplace,
    DriverResponse,
    DriverStatusResult,
    DriverStatusUpdate,
    ResourceStatus,
)


router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get("")
def list_drivers(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    resource_status: Annotated[ResourceStatus | None, Query(alias="status")] = None,
) -> ApiResponse[PaginatedData[DriverResponse]]:
    items, total = FleetService(db).list_drivers(
        page=pagination.page,
        page_size=pagination.page_size,
        status=resource_status.value if resource_status is not None else None,
    )
    data = PaginatedData.from_items(
        items=[DriverResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(data=data, message="Drivers retrieved", request_id=request_id)


@router.get("/{driver_id}")
def get_driver(
    driver_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DriverResponse]:
    data = DriverResponse.model_validate(FleetService(db).get_driver(driver_id))
    return success_response(data=data, message="Driver retrieved", request_id=request_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_driver(
    request: DriverCreate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DriverResponse]:
    driver = FleetService(db).create_driver(
        driver_code=request.driver_code,
        name=request.name,
    )
    return success_response(
        data=DriverResponse.model_validate(driver),
        message="Driver created",
        request_id=request_id,
    )


@router.put("/{driver_id}")
def replace_driver(
    driver_id: UUID,
    request: DriverReplace,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DriverResponse]:
    driver = FleetService(db).replace_driver(driver_id, name=request.name)
    return success_response(
        data=DriverResponse.model_validate(driver),
        message="Driver updated",
        request_id=request_id,
    )


@router.patch("/{driver_id}/status")
def update_driver_status(
    driver_id: UUID,
    request: DriverStatusUpdate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DriverStatusResult]:
    driver, manual_intervention_required = FleetService(db).update_driver_status(
        driver_id,
        status=request.status.value,
    )
    data = DriverStatusResult(
        driver=DriverResponse.model_validate(driver),
        manual_intervention_required=manual_intervention_required,
    )
    return success_response(
        data=data,
        message="Driver status updated",
        request_id=request_id,
    )
