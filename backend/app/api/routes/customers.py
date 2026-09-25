from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.resources.parties import PartyService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.resources import CustomerCreate, CustomerReplace, CustomerResponse


router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("")
def list_customers(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[PaginatedData[CustomerResponse]]:
    items, total = PartyService(db).list_customers(
        page=pagination.page,
        page_size=pagination.page_size,
    )
    data = PaginatedData.from_items(
        items=[CustomerResponse.model_validate(item) for item in items],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return success_response(data=data, message="Customers retrieved", request_id=request_id)


@router.get("/{customer_id}")
def get_customer(
    customer_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[CustomerResponse]:
    data = CustomerResponse.model_validate(PartyService(db).get_customer(customer_id))
    return success_response(data=data, message="Customer retrieved", request_id=request_id)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_customer(
    request: CustomerCreate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[CustomerResponse]:
    location = request.default_delivery_location
    customer = PartyService(db).create_customer(
        customer_code=request.customer_code,
        name=request.name,
        location_code=location.location_code,
        address_text=location.address_text,
        latitude=location.latitude,
        longitude=location.longitude,
    )
    return success_response(
        data=CustomerResponse.model_validate(customer),
        message="Customer created",
        request_id=request_id,
    )


@router.put("/{customer_id}")
def replace_customer(
    customer_id: UUID,
    request: CustomerReplace,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[CustomerResponse]:
    location = request.default_delivery_location
    customer = PartyService(db).replace_customer(
        customer_id,
        name=request.name,
        location_code=location.location_code,
        address_text=location.address_text,
        latitude=location.latitude,
        longitude=location.longitude,
    )
    return success_response(
        data=CustomerResponse.model_validate(customer),
        message="Customer updated",
        request_id=request_id,
    )
