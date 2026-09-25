from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.incidents.workflow import MerchantDelayWorkflow
from app.modules.resources.parties import PartyService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.resources import (
    MerchantCreate,
    MerchantReadyTimeUpdate,
    MerchantReplace,
    MerchantResponse,
    MerchantStatus,
    MerchantStatusUpdate,
)


router = APIRouter(prefix="/merchants", tags=["merchants"])


@router.get("")
def list_merchants(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    preparation_status: MerchantStatus | None = None,
) -> ApiResponse[PaginatedData[MerchantResponse]]:
    items, total = PartyService(db).list_merchants(
        page=pagination.page,
        page_size=pagination.page_size,
        preparation_status=(
            preparation_status.value if preparation_status is not None else None
        ),
    )
    data = PaginatedData.from_items(
        items=[MerchantResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(
        data=data,
        message="Merchants retrieved",
        request_id=request_id,
    )


@router.get("/{merchant_id}")
def get_merchant(
    merchant_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[MerchantResponse]:
    data = MerchantResponse.model_validate(PartyService(db).get_merchant(merchant_id))
    return success_response(data=data, message="Merchant retrieved", request_id=request_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_merchant(
    request: MerchantCreate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[MerchantResponse]:
    location = request.pickup_location
    merchant = PartyService(db).create_merchant(
        merchant_code=request.merchant_code,
        name=request.name,
        location_code=location.location_code,
        address_text=location.address_text,
        latitude=location.latitude,
        longitude=location.longitude,
        operational_ready_at=request.operational_ready_at,
        default_pickup_service_seconds=request.default_pickup_service_seconds,
    )
    return success_response(
        data=MerchantResponse.model_validate(merchant),
        message="Merchant created",
        request_id=request_id,
    )


@router.put("/{merchant_id}")
def replace_merchant(
    merchant_id: UUID,
    request: MerchantReplace,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[MerchantResponse]:
    location = request.pickup_location
    merchant = PartyService(db).replace_merchant(
        merchant_id,
        name=request.name,
        location_code=location.location_code,
        address_text=location.address_text,
        latitude=location.latitude,
        longitude=location.longitude,
        default_pickup_service_seconds=request.default_pickup_service_seconds,
    )
    return success_response(
        data=MerchantResponse.model_validate(merchant),
        message="Merchant updated",
        request_id=request_id,
    )


@router.patch("/{merchant_id}/status")
def update_merchant_status(
    merchant_id: UUID,
    request: MerchantStatusUpdate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[MerchantResponse]:
    merchant = PartyService(db).update_merchant_status(
        merchant_id,
        status=request.status.value,
    )
    return success_response(
        data=MerchantResponse.model_validate(merchant),
        message="Merchant status updated",
        request_id=request_id,
    )


@router.patch("/{merchant_id}/ready-time")
def update_merchant_ready_time(
    merchant_id: UUID,
    request: MerchantReadyTimeUpdate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[MerchantResponse]:
    result = MerchantDelayWorkflow(db).assess_delay(
        business_date=request.business_date,
        merchant_id=merchant_id,
        updated_ready_at=request.updated_ready_at,
        detected_at=request.detected_at,
        explicit_incident=False,
    )
    return success_response(
        data=MerchantResponse.model_validate(result.merchant),
        message="Merchant ready time updated",
        request_id=request_id,
    )
