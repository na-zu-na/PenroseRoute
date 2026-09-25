from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_request_id
from app.core.responses import success_response
from app.schemas.common import ApiResponse


router = APIRouter(tags=["health"])


@router.get("/health")
def health(
    request_id: Annotated[str, Depends(get_request_id)],
) -> ApiResponse[dict[str, str]]:
    return success_response(
        data={"status": "ok"},
        message="Service is healthy",
        request_id=request_id,
    )
