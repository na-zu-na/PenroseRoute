from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.planning.queries import PlanningQueryService
from app.schemas.common import ApiResponse
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
