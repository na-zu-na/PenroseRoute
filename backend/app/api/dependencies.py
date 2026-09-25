from collections.abc import Generator
from uuid import uuid4

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.core.config import get_settings


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def ensure_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = f"req_{uuid4().hex}"
        request.state.request_id = request_id
    return request_id


def get_request_id(request: Request) -> str:
    return ensure_request_id(request)


def get_recovery_mode() -> str:
    return get_settings().recovery_orchestration_mode


def get_recovery_explanation_client(mode: str = Depends(get_recovery_mode)):
    settings = get_settings()
    if mode != "agent" or settings.agent_explanation_provider == "template":
        return None
    if not settings.bedrock_model_id:
        return None
    from app.integrations.agent.client import BedrockExplanationClient
    return BedrockExplanationClient(
        settings.bedrock_model_id,
        region_name=settings.aws_region,
        endpoint_url=settings.bedrock_endpoint_url,
        connect_timeout=settings.agent_connect_timeout_seconds,
        read_timeout=settings.agent_read_timeout_seconds,
    )
