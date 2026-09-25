from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.operations.execution import StopExecutionService
from app.modules.planning.queries import PlanningQueryService
from app.schemas.common import ApiResponse
from app.schemas.operations import StopActionRequest, StopExecutionResponse
from app.schemas.planning import RouteStopResponse


router = APIRouter(prefix="/route-stops", tags=["route-stops"])


@router.get("/{stop_id}")
def get_route_stop(
    stop_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[RouteStopResponse]:
    data = RouteStopResponse.model_validate(
        PlanningQueryService(db).get_stop(stop_id)
    )
    return success_response(
        data=data,
        message="Route stop retrieved",
        request_id=request_id,
    )


def _execute_action(
    *,
    action: str,
    stop_id: UUID,
    request: StopActionRequest,
    request_id: str,
    db: Session,
) -> ApiResponse[StopExecutionResponse]:
    service = StopExecutionService(db)
    result = getattr(service, action)(stop_id, request.occurred_at)
    return success_response(
        data=StopExecutionResponse.model_validate(result),
        message="Route stop execution updated",
        request_id=request_id,
    )


@router.post("/{stop_id}/arrive")
def arrive_at_route_stop(
    stop_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    request: StopActionRequest = Body(default_factory=StopActionRequest),
) -> ApiResponse[StopExecutionResponse]:
    return _execute_action(
        action="arrive",
        stop_id=stop_id,
        request=request,
        request_id=request_id,
        db=db,
    )


@router.post("/{stop_id}/start-service")
def start_route_stop_service(
    stop_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    request: StopActionRequest = Body(default_factory=StopActionRequest),
) -> ApiResponse[StopExecutionResponse]:
    return _execute_action(
        action="start_service",
        stop_id=stop_id,
        request=request,
        request_id=request_id,
        db=db,
    )


@router.post("/{stop_id}/complete")
def complete_route_stop(
    stop_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    request: StopActionRequest = Body(default_factory=StopActionRequest),
) -> ApiResponse[StopExecutionResponse]:
    return _execute_action(
        action="complete",
        stop_id=stop_id,
        request=request,
        request_id=request_id,
        db=db,
    )
