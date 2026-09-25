"""Small single-workspace API-key authentication; replace with SSO dependency later."""
import hmac
import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header

from app.core.config import get_settings
from app.integrations.agent.contracts import RecoveryError


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str


def authenticate_dispatch_user(authorization: Annotated[str | None, Header()] = None) -> Principal:
    configured = get_settings().dispatch_api_tokens
    if configured is None:
        raise RecoveryError("AUTH_NOT_CONFIGURED", "请配置调度 API 身份凭据", 503)
    try:
        entries = json.loads(configured.get_secret_value())
        if not isinstance(entries, dict) or not entries:
            raise ValueError()
        for token, identity in entries.items():
            if (len(token) < 32 or identity["role"] not in ("reader", "dispatcher")
                    or not isinstance(identity["subject"], str) or not identity["subject"].strip()):
                raise ValueError()
    except (ValueError, KeyError, TypeError) as exc:
        raise RecoveryError("AUTH_CONFIG_INVALID", "调度身份配置无效", 503) from exc
    if not authorization or not authorization.startswith("Bearer "):
        raise RecoveryError("UNAUTHORIZED", "需要 Bearer 凭据", 401)
    supplied = authorization[7:]
    for token, identity in entries.items():
        if hmac.compare_digest(supplied.encode(), token.encode()):
            return Principal(identity["subject"], identity["role"])
    raise RecoveryError("UNAUTHORIZED", "身份凭据无效", 401)
