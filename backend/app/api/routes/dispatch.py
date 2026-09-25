from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.auth import Principal, authenticate_dispatch_user
from app.core.config import get_settings
from app.integrations.agent.contracts import RecoveryError
from app.integrations.dispatch_agent.contracts import DispatchCommand
from app.integrations.dispatch_agent.planner import BedrockIntentPlanner
from app.modules.dispatch.queries import DispatchQueries
from app.modules.dispatch.service import DispatchService
from app.modules.dispatch.session import ContextCodec

router = APIRouter(prefix="/agent", tags=["Dispatch Agent"])


@lru_cache
def intent_planner(model_id, region):
    return BedrockIntentPlanner(model_id, region)


def get_dispatch_service(request: Request):
    override = getattr(request.app.state, "dispatch_service", None)
    if override is not None:
        return override
    settings = get_settings()
    if not settings.dispatch_context_secret or len(settings.dispatch_context_secret.get_secret_value()) < 32:
        raise RecoveryError("DISPATCH_NOT_CONFIGURED", "请设置至少 32 字符的上下文签名密钥", 503)
    from app.db.session import SessionLocal
    planner = None
    if settings.dispatch_intent_provider == "bedrock":
        if not settings.bedrock_model_id:
            raise RecoveryError("DISPATCH_NOT_CONFIGURED", "请配置 BEDROCK_MODEL_ID", 503)
        planner = intent_planner(settings.bedrock_model_id, settings.aws_region)
    from app.api.routes.recovery import get_agent_recovery_workflow
    return DispatchService(DispatchQueries(SessionLocal),
        codec=ContextCodec(settings.dispatch_context_secret.get_secret_value()), planner=planner,
        timezone=settings.business_timezone,
        recovery=get_agent_recovery_workflow(request))


@router.post("/dispatch")
def dispatch(command: DispatchCommand, request: Request,
             principal: Principal = Depends(authenticate_dispatch_user),
             service: DispatchService = Depends(get_dispatch_service)):
    result = service.run(command, principal.subject, can_generate=principal.role == "dispatcher")
    code = {"COMPLETED": "DISPATCH_COMPLETED", "NEEDS_INPUT": "DISPATCH_NEEDS_INPUT", "FAILED": "DISPATCH_FAILED"}[result.status]
    return JSONResponse(status_code=200, content={
        "success": result.status != "FAILED", "code": code, "message": result.message,
        "data": result.model_dump(mode="json"), "request_id": "req_" + uuid4().hex,
    })
