from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.planning.queries import PlanningQueryService
from app.schemas.common import ApiResponse
from app.schemas.planning import RouteStopResponse, VehicleRouteResponse


router = APIRouter(prefix="/vehicle-routes", tags=["vehicle-routes"])


@router.get("/{route_id}")
def get_vehicle_route(
    route_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[VehicleRouteResponse]:
    data = VehicleRouteResponse.model_validate(
        PlanningQueryService(db).get_route(route_id)
    )
    return success_response(
        data=data,
        message="Vehicle route retrieved",
        request_id=request_id,
    )


@router.get("/{route_id}/stops")
def list_vehicle_route_stops(
    route_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[list[RouteStopResponse]]:
    stops = PlanningQueryService(db).list_route_stops(route_id)
    data = [RouteStopResponse.model_validate(stop) for stop in stops]
    return success_response(
        data=data,
        message="Vehicle route stops retrieved",
        request_id=request_id,
    )
