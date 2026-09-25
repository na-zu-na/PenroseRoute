from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Action = Literal[
    "get_resource_availability", "get_delivery_status",
    "start_incident_recovery", "get_recovery_proposal", "compare_plan_versions",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DispatchContext(StrictModel):
    business_date: date | None = None
    incident_id: UUID | None = None
    recovery_plan_id: UUID | None = None
    base_plan_id: UUID | None = None
    candidate_plan_id: UUID | None = None


class DispatchCommand(StrictModel):
    message: str = Field(min_length=1, max_length=3000)
    context: DispatchContext = Field(default_factory=DispatchContext)
    context_token: str | None = Field(default=None, max_length=8000)


class IntentPlan(StrictModel):
    actions: tuple[Action, ...] = Field(default=(), max_length=6)
    clarification: str | None = Field(default=None, max_length=500)


class ToolObservation(StrictModel):
    tool: Action
    success: bool
    code: str
    data: dict | None = None


class DispatchReply(StrictModel):
    status: Literal["COMPLETED", "NEEDS_INPUT", "FAILED"]
    message: str
    context: DispatchContext
    observations: tuple[ToolObservation, ...] = ()
    planner_source: Literal["rules", "model", "fallback"] = "rules"
    context_token: str | None = None
