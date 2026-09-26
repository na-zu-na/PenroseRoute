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
        from app.core.errors import AuthenticationError
        raise AuthenticationError("DISPATCH_NOT_CONFIGURED", "请设置至少 32 字符的上下文签名密钥", 503)
    from app.db.session import SessionLocal
    planner = None
    if settings.dispatch_intent_provider == "bedrock":
        if not settings.bedrock_model_id:
            planner = None
        else:
            try:
                planner = intent_planner(settings.bedrock_model_id, settings.aws_region)
            except Exception:
                # Defer the failure to the coordinator so the rule fallback is labelled.
                class UnavailablePlanner:
                    def plan(self, *_):
                        raise RuntimeError("Intent model unavailable")
                planner = UnavailablePlanner()
    from app.api.dependencies import get_recovery_explanation_client
    explanation_client = get_recovery_explanation_client(mode="agent")
    return DispatchService(DispatchQueries(SessionLocal),
        codec=ContextCodec(settings.dispatch_context_secret.get_secret_value()), planner=planner,
        timezone=settings.business_timezone, explanation_client=explanation_client)


@router.post("/dispatch")
def dispatch(command: DispatchCommand, request: Request,
             principal: Principal = Depends(authenticate_dispatch_user),
             service: DispatchService = Depends(get_dispatch_service)):
    try:
        result = service.run(command, principal.subject)
    except RecoveryError as exc:
        from app.core.errors import AuthenticationError
        raise AuthenticationError(exc.code, str(exc), exc.http_status) from exc
    code = {"COMPLETED": "DISPATCH_COMPLETED", "NEEDS_INPUT": "DISPATCH_NEEDS_INPUT", "FAILED": "DISPATCH_FAILED"}[result.status]
    return JSONResponse(status_code=200, content={
        "success": result.status != "FAILED", "code": code, "message": result.message,
        "data": result.model_dump(mode="json"), "request_id": getattr(request.state, "request_id", "req_" + uuid4().hex),
    })
