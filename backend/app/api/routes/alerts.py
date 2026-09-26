"""Read-only persisted alert endpoints; GET never evaluates risk."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.operations.alert_queries import AlertQueryService
from app.schemas.alerts import AlertChangeResponse, AlertChangesPage, AlertResponse
from app.schemas.common import ApiResponse, PaginatedData, PaginationParams


router = APIRouter(prefix="/operations/alerts", tags=["operations"])


@router.get("")
def list_alerts(
    pagination: Annotated[PaginationParams, Depends()],
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    business_date: date | None = None,
    status: Literal["ACTIVE", "RESOLVED"] = "ACTIVE",
) -> ApiResponse[PaginatedData[AlertResponse]]:
    rows, total = AlertQueryService(db).list_alerts(
        business_date, status, pagination.page, pagination.page_size,
    )
    return success_response(
        data=PaginatedData.from_items(
            items=[AlertResponse.model_validate(row) for row in rows],
            page=pagination.page, page_size=pagination.page_size, total=total,
        ),
        message="Risk alerts retrieved", request_id=request_id,
    )


@router.get("/changes")
def list_alert_changes(
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
    after: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ApiResponse[AlertChangesPage]:
    rows = AlertQueryService(db).list_changes(after, limit)
    return success_response(
        data=AlertChangesPage(
            items=[AlertChangeResponse.model_validate(row) for row in rows],
            next_cursor=rows[-1].change_id if rows else after,
        ),
        message="Risk alert changes retrieved", request_id=request_id,
    )
