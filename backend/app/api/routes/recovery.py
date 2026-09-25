"""Recovery endpoint: dependencies own authentication and application wiring."""
from typing import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.auth import Principal, authenticate_dispatch_user
from app.integrations.agent.contracts import RecoveryError
from app.modules.recovery.workflow import RecoveryWorkflow


class EmptyRecoveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


def require_operations_user(principal: Principal = Depends(authenticate_dispatch_user)) -> Principal:
    if principal.role != "dispatcher":
        raise RecoveryError("DISPATCH_FORBIDDEN", "需要调度员权限", 403)
    return principal


def get_recovery_workflow(request: Request) -> RecoveryWorkflow:
    workflow = getattr(request.app.state, "recovery_workflow", None)
    if workflow is None:
        from app.core.config import get_settings
        from app.db.session import SessionLocal
        from app.modules.recovery.bootstrap import create_default_recovery_workflow
        workflow = create_default_recovery_workflow(SessionLocal, get_settings())
    return workflow


def build_recovery_router(
    workflow: RecoveryWorkflow | None = None,
    operations_user_dependency: Callable = require_operations_user,
    prefix: str = "/api",
) -> APIRouter:
    router = APIRouter(prefix=prefix, dependencies=[Depends(operations_user_dependency)])
    provider = (lambda: workflow) if workflow is not None else get_recovery_workflow

    @router.post("/incidents/{incident_id}/recovery", status_code=201)
    def recover(incident_id: UUID, body: EmptyRecoveryCommand, request: Request,
                service: RecoveryWorkflow = Depends(provider)):
        request_id = getattr(request.state, "request_id", None) or "req_" + uuid4().hex
        try:
            reply = service.run(incident_id)
            return JSONResponse(status_code=reply.http_status, content=reply.envelope(request_id))
        except RecoveryError as exc:
            return JSONResponse(status_code=exc.http_status, content={
                "success": False, "code": exc.code, "message": str(exc),
                "data": None, "request_id": request_id})

    return router


router = build_recovery_router(prefix="")
