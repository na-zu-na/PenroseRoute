from datetime import datetime
from typing import Any
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


class RecoveryAttemptSummary(BaseModel):
    recovery_plan_id: UUID
    attempt_no: int
    replanning_scope: str
    status: str
    solver_status: str | None
    validation_status: str | None
    candidate_delivery_plan_id: UUID | None


class RecoveryPlanDetailResponse(RecoveryAttemptSummary):
    recovery_code: str
    incident_id: UUID
    previous_recovery_plan_id: UUID | None
    base_delivery_plan_id: UUID
    base_plan_code: str
    candidate_plan_code: str | None
    scope_description: str
    agent_explanation: str | None
    solver_validation_summary: dict[str, Any] | None
    dispatcher_decision: str | None
    decision_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
