"""Integration DTOs, not ORM models or public HTTP schemas. All IDs are UUIDs."""
import json
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Scope = Literal["AFFECTED_ROUTE", "CROSS_ROUTE", "ALL_REMAINING"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImpactFact(Contract):
    """Already computed by incidents/impact.py. Agent never infers these flags."""
    order_id: UUID
    execution_status_snapshot: Literal["PLANNED", "PICKUP_IN_PROGRESS", "PICKED_UP", "DELIVERING", "COMPLETED"]
    risk_status_snapshot: Literal["NORMAL", "AT_RISK"]
    requires_replanning: bool
    handover_required: bool
    was_completed: bool
    impact_type: Literal["COMPLETED_FROZEN", "HANDOVER_REQUIRED", "PICKUP_REPLAN", "WAITING_TIME_UPDATE", "DOWNSTREAM_ROUTE_IMPACT"]


class RecoveryContext(Contract):
    recovery_plan_id: UUID
    incident_id: UUID
    business_date: date
    base_delivery_plan_id: UUID
    attempt_no: int = Field(gt=0)
    previous_recovery_plan_id: UUID | None = None
    incident_type: Literal["VEHICLE_UNAVAILABLE", "MERCHANT_DELAY"]
    requires_replanning: bool
    replanning_scope: Scope
    scope_description: str = Field(min_length=1, max_length=2000)
    current_time: datetime
    # Application-only concurrency token; no new P0 DB column required.
    snapshot_token: str = Field(min_length=1)
    travel_time_source: Literal["GEOGRAPHIC_ESTIMATE", "ROAD_MATRIX", "UNSPECIFIED"] = "UNSPECIFIED"
    affected_orders: tuple[ImpactFact, ...]
    frozen_stop_ids: tuple[UUID, ...] = ()
    incident_location_id: UUID | None = None
    delay_seconds: int | None = Field(default=None, ge=0)
    # Materialized business projections, never ORM objects or model-authored facts.
    incident_summary: str = ""
    incident_facts: dict = Field(default_factory=dict)
    current_plan_summary: dict = Field(default_factory=dict)
    available_resources: tuple[dict, ...] = ()
    previous_attempts: tuple[dict, ...] = ()

    @field_validator("current_time")
    @classmethod
    def timezone_required(cls, value):
        if value.utcoffset() is None:
            raise ValueError("current_time must have an explicit timezone")
        return value

    @model_validator(mode="after")
    def attempt_chain(self):
        if (self.attempt_no == 1) != (self.previous_recovery_plan_id is None):
            raise ValueError("previous_recovery_plan_id must match attempt_no")
        if len({o.order_id for o in self.affected_orders}) != len(self.affected_orders):
            raise ValueError("duplicate affected order")
        return self


class SolverResult(Contract):
    status: Literal["FEASIBLE", "INFEASIBLE", "ERROR"]
    # Serialized *existing* Optimization Contract output, never model-written.
    # Local transport only; finalizer must map it into the 14 formal P0 tables.
    solution_payload_json: str | None = None
    diagnostic_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def consistent_result(self):
        if self.status == "FEASIBLE":
            if not self.solution_payload_json or not isinstance(json.loads(self.solution_payload_json), dict):
                raise ValueError("FEASIBLE requires a serialized optimization result object")
        elif self.solution_payload_json is not None:
            raise ValueError("failed solve cannot carry a solution")
        return self


class VerifiedSummary(Contract):
    """Supplied by the deterministic validator, not inferred from LLM text."""
    assigned_order_count: int = Field(ge=0)
    unassigned_order_ids: tuple[UUID, ...] = ()
    changed_route_count: int = Field(ge=0)
    total_distance_meters: int = Field(ge=0)
    total_duration_seconds: int = Field(ge=0)


class ValidationReport(Contract):
    status: Literal["VALID", "INVALID"]
    summary: VerifiedSummary | None = None
    diagnostic_codes: tuple[str, ...] = ()
    recovery_evidence: "RecoveryEvidence | None" = None

    @model_validator(mode="after")
    def verified_summary_only(self):
        if (self.status == "VALID") != (self.summary is not None):
            raise ValueError("only VALID results carry verified summary")
        if self.status != "VALID" and self.recovery_evidence is not None:
            raise ValueError("invalid results cannot carry verified recovery evidence")
        return self


class OrderReassignment(Contract):
    order_id: UUID
    from_vehicle_id: UUID | None
    to_vehicle_id: UUID | None


class PlanMetrics(Contract):
    assigned_order_count: int = Field(ge=0)
    unassigned_order_count: int = Field(ge=0)
    vehicle_count: int = Field(ge=0)
    total_distance_meters: int = Field(ge=0)
    total_duration_seconds: int = Field(ge=0)


class RecoveryEvidence(Contract):
    """Business-layer comparison of the full candidate, after solver validation."""
    reassigned_orders: tuple[OrderReassignment, ...] = ()
    unchanged_order_ids: tuple[UUID, ...] = ()
    changed_order_ids: tuple[UUID, ...] = ()
    handover_order_ids: tuple[UUID, ...] = ()
    before: PlanMetrics
    after: PlanMetrics
    changed_vehicle_ids: tuple[UUID, ...] = ()
    remaining_risks: tuple[str, ...] = ()


class RecoveryExplanation(Contract):
    summary: str
    impact_explanation: str
    replanning_explanation: str
    result_explanation: str
    remaining_risks: tuple[str, ...]


class Observation(Contract):
    solver: SolverResult
    validation: ValidationReport | None = None

    @model_validator(mode="after")
    def validation_lifecycle(self):
        if (self.solver.status == "FEASIBLE") != (self.validation is not None):
            raise ValueError("only feasible results must be validated")
        return self


class ExplanationFact(Contract):
    id: str
    text: str


class ExplanationOutline(Contract):
    """The model arranges trusted facts, not numbers, statuses or execution commands."""
    fact_ids: tuple[str, ...] = Field(min_length=1, max_length=20)


class AgentResult(Contract):
    recovery_plan_id: UUID
    observation: Observation
    agent_explanation: str = Field(min_length=1)
    explanation_source: Literal["model", "template", "fallback"]
    diagnostic_codes: tuple[str, ...] = ()
    tool_trace: tuple[str, ...]
    explanation: RecoveryExplanation | None = None

    @property
    def reviewable(self):
        return self.observation.solver.status == "FEASIBLE" and self.observation.validation.status == "VALID"


class AttemptRecord(Contract):
    """Persisted recovery_plans projection returned by application finalizer."""
    recovery_plan_id: UUID
    incident_id: UUID
    attempt_no: int = Field(gt=0)
    previous_recovery_plan_id: UUID | None
    replanning_scope: Scope
    status: Literal["DRAFT", "PENDING_REVIEW"]
    solver_status: Literal["FEASIBLE", "INFEASIBLE", "ERROR"]
    validation_status: Literal["VALID", "INVALID"] | None
    candidate_delivery_plan_id: UUID | None
    agent_explanation: str

    @model_validator(mode="after")
    def enforce_database_states(self):
        good = self.solver_status == "FEASIBLE" and self.validation_status == "VALID"
        if (self.solver_status == "FEASIBLE") != (self.validation_status is not None):
            raise ValueError("invalid validation lifecycle")
        if good != (self.status == "PENDING_REVIEW") or good != (self.candidate_delivery_plan_id is not None):
            raise ValueError("only FEASIBLE + VALID has candidate and PENDING_REVIEW")
        if good and not self.agent_explanation.strip():
            raise ValueError("reviewable recovery requires explanation")
        if (self.attempt_no == 1) != (self.previous_recovery_plan_id is None):
            raise ValueError("invalid attempt chain")
        return self


class RecoveryError(Exception):
    def __init__(self, code, message="", http_status=500):
        self.code, self.http_status = code, http_status
        super().__init__(message or code)
