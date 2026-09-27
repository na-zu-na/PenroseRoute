from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, get_request_id
from app.core.responses import success_response
from app.modules.planning.workflow import PlanningWorkflow
from app.schemas.common import ApiResponse
from app.schemas.planning import DeliveryPlanResponse, GeneratePlanRequest, GeneratePlanResponse


router = APIRouter(prefix="/planning", tags=["planning"])


@router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate_plan(
    request: GeneratePlanRequest,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[GeneratePlanResponse]:
    result = PlanningWorkflow(db).generate(request.business_date)
    return success_response(
        data=GeneratePlanResponse.model_validate(result),
        code="PLAN_CREATED",
        message="Current delivery plan created",
        request_id=request_id,
    )


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def generate_draft(
    request: GeneratePlanRequest,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[GeneratePlanResponse]:
    result = PlanningWorkflow(db).generate_draft(request.business_date)
    return success_response(
        data=GeneratePlanResponse.model_validate(result),
        code="PLAN_DRAFT_CREATED",
        message="Delivery plan draft is ready for review",
        request_id=request_id,
    )


@router.post("/drafts/{plan_id}/confirm")
def confirm_draft(
    plan_id: UUID,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[DeliveryPlanResponse]:
    result = PlanningWorkflow(db).confirm_draft(plan_id)
    return success_response(
        data=DeliveryPlanResponse.model_validate(result),
        code="PLAN_CONFIRMED",
        message="Delivery plan is now current",
        request_id=request_id,
    )
