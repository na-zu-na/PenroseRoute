"""P0 Recovery command; the public API never exposes the orchestration choice."""
from typing import Annotated, Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.auth import Principal, authenticate_dispatch_user
from app.api.dependencies import get_db, get_request_id
from app.core.errors import AuthenticationError
from app.core.responses import success_response
from app.modules.recovery.deterministic_workflow import RecoveryWorkflow
from app.schemas.common import ApiResponse
from app.schemas.recovery import StartRecoveryRequest, StartRecoveryResponse


router = APIRouter(prefix="/incidents", tags=["recovery"])


def require_operations_user(principal: Principal = Depends(authenticate_dispatch_user)) -> Principal:
    if principal.role != "dispatcher":
        raise AuthenticationError("DISPATCH_FORBIDDEN", "需要调度员权限", 403)
    return principal


@router.post(
    "/{incident_id}/recovery",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operations_user)],
)
def start_recovery(
    incident_id: UUID,
    request: StartRecoveryRequest,
    request_id: Annotated[str, Depends(get_request_id)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiResponse[StartRecoveryResponse]:
    del request
    result = RecoveryWorkflow(db).start(incident_id)
    code = "RECOVERY_PENDING_REVIEW" if result.outcome == "PENDING_REVIEW" else "NO_FEASIBLE_RECOVERY"
    message = (
        "A valid recovery candidate is ready for dispatcher review"
        if result.outcome == "PENDING_REVIEW"
        else "No feasible recovery was found; manual intervention is required"
    )
    return success_response(
        data=StartRecoveryResponse.model_validate(result),
        code=code,
        message=message,
        request_id=request_id,
    )


# Legacy Agent router is kept for isolated Agent tests and future integration.
# The P0 application never constructs or registers it.
class EmptyRecoveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_agent_recovery_workflow(request: Request):
    workflow = getattr(request.app.state, "recovery_workflow", None)
    if workflow is None:
        from app.core.config import get_settings
        from app.db.session import SessionLocal
        from app.modules.recovery.bootstrap import create_default_recovery_workflow
        workflow = create_default_recovery_workflow(SessionLocal, get_settings())
    return workflow


def build_recovery_router(
    workflow=None,
    operations_user_dependency: Callable = require_operations_user,
    prefix: str = "/api",
) -> APIRouter:
    from app.integrations.agent.contracts import RecoveryError

    legacy_router = APIRouter(prefix=prefix, dependencies=[Depends(operations_user_dependency)])
    provider = (lambda: workflow) if workflow is not None else get_agent_recovery_workflow

    @legacy_router.post("/incidents/{incident_id}/recovery", status_code=201)
    def recover(incident_id: UUID, body: EmptyRecoveryCommand, request: Request,
                service=Depends(provider)):
        request_id = getattr(request.state, "request_id", None) or "req_" + uuid4().hex
        try:
            reply = service.run(incident_id)
            return JSONResponse(status_code=reply.http_status, content=reply.envelope(request_id))
        except RecoveryError as exc:
            return JSONResponse(status_code=exc.http_status, content={
                "success": False, "code": exc.code, "message": str(exc),
                "data": None, "request_id": request_id})

    return legacy_router
