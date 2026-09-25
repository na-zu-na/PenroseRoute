from uuid import UUID, uuid4
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.api.auth import Principal
from app.api.routes.recovery import require_operations_user, get_recovery_workflow
from app.modules.decisions.service import DecisionService

router = APIRouter(prefix="/api/recovery-plans", tags=["Human decisions"])

class DecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Reason cannot be blank")
        return value.strip()


def get_decision_service():
    from app.db.session import SessionLocal
    return DecisionService(SessionLocal)


def envelope(data):
    return {"success": True, "code": "RECOVERY_DECIDED", "message": "人工决定已保存", "data": data, "request_id": "req_"+uuid4().hex}

@router.post("/{recovery_id}/approve")
def approve(recovery_id: UUID, command: DecisionCommand,
            principal: Principal = Depends(require_operations_user), service=Depends(get_decision_service)):
    return envelope(service.decide(recovery_id, "APPROVE", command.reason, principal.subject))

@router.post("/{recovery_id}/reject")
def reject(recovery_id: UUID, command: DecisionCommand,
           principal: Principal = Depends(require_operations_user), service=Depends(get_decision_service)):
    return envelope(service.decide(recovery_id, "REJECT", command.reason, principal.subject))


@router.post("/{recovery_id}/modify")
def modify(recovery_id: UUID, command: DecisionCommand,
           principal: Principal = Depends(require_operations_user), service=Depends(get_decision_service),
           workflow=Depends(get_recovery_workflow)):
    from fastapi.responses import JSONResponse
    reply = service.modify(recovery_id, command.reason, principal.subject, workflow)
    return JSONResponse(status_code=reply.http_status, content=reply.envelope("req_"+uuid4().hex))
