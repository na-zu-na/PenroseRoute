from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.db.models.fleet import AssignmentStatus as DatabaseAssignmentStatus
from app.modules.resources.assignments import AssignmentService
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams
from app.schemas.resources import AssignmentCreate, AssignmentResponse, AssignmentStatus


router = APIRouter(prefix="/vehicle-driver-assignments", tags=["vehicle-driver-assignments"])


@router.get("")
def list_assignments(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    business_date: date | None = None,
    status: AssignmentStatus | None = None,
    vehicle_id: UUID | None = None,
    driver_id: UUID | None = None,
) -> ApiResponse[PaginatedData[AssignmentResponse]]:
    items, total = AssignmentService(db).list_assignments(
        page=pagination.page, page_size=pagination.page_size, business_date=business_date,
        status=DatabaseAssignmentStatus(status.value) if status else None,
        vehicle_id=vehicle_id, driver_id=driver_id,
    )
    return success_response(
        data=PaginatedData.from_items(
            items=[AssignmentResponse.model_validate(item) for item in items],
            page=pagination.page, page_size=pagination.page_size, total=total,
        ),
        message="Vehicle-driver assignments retrieved", request_id=request_id,
    )


@router.get("/{assignment_id}")
def get_assignment(
    assignment_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[AssignmentResponse]:
    return success_response(
        data=AssignmentResponse.model_validate(AssignmentService(db).get_assignment(assignment_id)),
        message="Vehicle-driver assignment retrieved", request_id=request_id,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_assignment(
    request: AssignmentCreate,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[AssignmentResponse]:
    data = AssignmentService(db).create_assignment(**request.model_dump())
    return success_response(
        data=AssignmentResponse.model_validate(data),
        message="Vehicle-driver assignment created", request_id=request_id,
    )


@router.post("/{assignment_id}/activate")
def activate_assignment(
    assignment_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[AssignmentResponse]:
    data = AssignmentService(db).activate(assignment_id)
    return success_response(
        data=AssignmentResponse.model_validate(data),
        message="Vehicle-driver assignment activated", request_id=request_id,
    )


@router.post("/{assignment_id}/end")
def end_assignment(
    assignment_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[AssignmentResponse]:
    data = AssignmentService(db).end(assignment_id)
    return success_response(
        data=AssignmentResponse.model_validate(data),
        message="Vehicle-driver assignment ended", request_id=request_id,
    )


@router.post("/{assignment_id}/cancel")
def cancel_assignment(
    assignment_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[AssignmentResponse]:
    data = AssignmentService(db).cancel(assignment_id)
    return success_response(
        data=AssignmentResponse.model_validate(data),
        message="Vehicle-driver assignment cancelled", request_id=request_id,
    )
