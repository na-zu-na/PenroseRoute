"""Signed, expiring context; no in-process conversation state or ORM objects."""
import base64
import hashlib
import hmac
import json
import time

from app.integrations.agent.contracts import RecoveryError
from app.integrations.dispatch_agent.contracts import DispatchContext, IntentPlan


class ContextCodec:
    def __init__(self, key: str, clock=time.time, ttl_seconds=1800):
        if len(key) < 32:
            raise ValueError("Dispatch context signing key must have at least 32 characters")
        self.key, self.clock, self.ttl = key.encode(), clock, ttl_seconds

    def encode(self, context: DispatchContext, subject: str, pending_actions=()):
        payload = json.dumps({"context": context.model_dump(mode="json"), "subject": subject,
            "expires": int(self.clock()) + self.ttl, "pending_actions": list(pending_actions)}, sort_keys=True, separators=(",", ":")).encode()
        body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()
        return body + "." + signature

    def decode(self, token: str, subject: str):
        return self.decode_state(token, subject)[0]

    def decode_state(self, token: str, subject: str):
        try:
            body, signature = token.split(".")
            expected = hmac.new(self.key, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("Signature mismatch")
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            if data["subject"] != subject or data["expires"] <= self.clock():
                raise ValueError("Expired or wrong subject")
            return (DispatchContext.model_validate(data["context"]),
                    IntentPlan(actions=tuple(data.get("pending_actions", ()))).actions)
        except Exception as exc:
            raise RecoveryError("CONTEXT_TOKEN_INVALID", "上下文已过期或不属于当前用户，请重新选择日期和方案", 400) from exc
