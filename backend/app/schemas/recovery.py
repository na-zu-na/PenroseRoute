from uuid import UUID

from pydantic import BaseModel, ConfigDict


class StartRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecoveryAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recovery_plan_id: UUID
    attempt_no: int
    replanning_scope: str
    status: str
    solver_status: str | None
    validation_status: str | None
    candidate_delivery_plan_id: UUID | None


class StartRecoveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    outcome: str
    attempts_created: tuple[RecoveryAttemptResponse, ...]
    reviewable_recovery_plan_id: UUID | None
    candidate_delivery_plan_id: UUID | None
    agent_explanation: str | None
    manual_intervention_required: bool = False
