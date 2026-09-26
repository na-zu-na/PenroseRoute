from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.recovery.queries import RecoveryQueryService
from app.modules.planning.comparison import PlanComparisonService
from app.schemas.common import ApiResponse
from app.schemas.planning import PlanComparisonResponse
from app.schemas.recovery import RecoveryPlanDetailResponse


router = APIRouter(prefix="/recovery-plans", tags=["recovery"])


@router.get("/{recovery_plan_id}")
def get_recovery_plan(
    recovery_plan_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[RecoveryPlanDetailResponse]:
    return success_response(
        data=RecoveryPlanDetailResponse.model_validate(
            RecoveryQueryService(db).get_attempt(recovery_plan_id)
        ),
        message="Recovery attempt retrieved",
        request_id=request_id,
    )


@router.get("/{recovery_plan_id}/comparison")
def compare_recovery_plan(
    recovery_plan_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[PlanComparisonResponse]:
    return success_response(
        data=PlanComparisonResponse.model_validate(
            PlanComparisonService(db).compare_recovery(recovery_plan_id)
        ),
        message="Recovery candidate comparison retrieved",
        request_id=request_id,
    )
