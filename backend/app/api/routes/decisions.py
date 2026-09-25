from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.api.auth import Principal
from app.api.dependencies import get_request_id
from app.api.routes.recovery import require_operations_user
from app.core.responses import success_response
from app.modules.decisions.service import DeterministicDecisionService

router = APIRouter(prefix="/recovery-plans", tags=["Human decisions"])

class DecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_reason: str = Field(min_length=1, max_length=2000)

    @field_validator("decision_reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Reason cannot be blank")
        return value.strip()


def get_decision_service():
    from app.db.session import SessionLocal
    return DeterministicDecisionService(SessionLocal)


def envelope(data, *, code: str, message: str, request_id: str):
    return success_response(data=data, code=code, message=message, request_id=request_id)

@router.post("/{recovery_id}/approve")
def approve(recovery_id: UUID, command: DecisionCommand,
            principal: Principal = Depends(require_operations_user), service=Depends(get_decision_service),
            request_id: str = Depends(get_request_id)):
    return envelope(service.decide(recovery_id, "APPROVE", command.decision_reason, principal.subject),
                    code="RECOVERY_APPROVED", message="Recovery plan approved and activated", request_id=request_id)

@router.post("/{recovery_id}/reject")
def reject(recovery_id: UUID, command: DecisionCommand,
           principal: Principal = Depends(require_operations_user), service=Depends(get_decision_service),
           request_id: str = Depends(get_request_id)):
    return envelope(service.decide(recovery_id, "REJECT", command.decision_reason, principal.subject),
                    code="RECOVERY_REJECTED", message="Recovery candidate rejected", request_id=request_id)


@router.post("/{recovery_id}/modify")
def modify(recovery_id: UUID, command: DecisionCommand,
           principal: Principal = Depends(require_operations_user), service=Depends(get_decision_service),
           request_id: str = Depends(get_request_id)):
    data = service.modify(recovery_id, command.decision_reason, principal.subject)
    return envelope(data, code="RECOVERY_MODIFICATION_PROCESSED",
                    message="The current candidate was cancelled and the new recovery attempt was processed",
                    request_id=request_id)
