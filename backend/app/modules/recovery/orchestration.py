import json
from dataclasses import asdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
from app.integrations.agent.client import ExplanationClient
from app.integrations.agent.contracts import Observation, RecoveryContext, SolverResult, ValidationReport
from app.integrations.agent.graph import run_agent
from app.integrations.agent.tools import ControlledTools
from .context import validate_context


@dataclass(frozen=True)
class PreparedAttempt:
    context: RecoveryContext
    # A serialization of your existing Optimization SolverInput, not a new DB field.
    # App input_builder owns exact schema, units and consistency with the context.
    optimization_input_json: str

    def __post_init__(self):
        if not isinstance(json.loads(self.optimization_input_json), dict):
            raise ValueError("Materialized solver input must be a JSON object")


class BoundReplanningCapability:
    def __init__(self, prepared, solver, validator, evidence_projector=None):
        self.prepared = prepared
        self.solver = solver
        self.validator = validator
        self.evidence_projector = evidence_projector

    def solve_replanning(self):
        try:
            result = SolverResult.model_validate(self.solver(self.prepared.optimization_input_json))
        except TimeoutError:
            # Never invent INFEASIBLE for an unknown/timeout outcome.
            result = SolverResult(status="ERROR", diagnostic_codes=("SOLVER_TIMEOUT",))
        except Exception:
            result = SolverResult(status="ERROR", diagnostic_codes=("SOLVER_EXECUTION_OR_CONTRACT_ERROR",))
        if result.status != "FEASIBLE":
            return Observation(solver=result)
        try:
            validation = ValidationReport.model_validate(self.validator(self.prepared.optimization_input_json, result))
            if validation.status == "VALID" and self.evidence_projector is not None:
                validation = ValidationReport.model_validate({**validation.model_dump(),
                    "recovery_evidence": self.evidence_projector(self.prepared.optimization_input_json, result)})
        except Exception:
            validation = ValidationReport(status="INVALID", diagnostic_codes=("VALIDATOR_EXECUTION_OR_CONTRACT_ERROR",))
        return Observation(solver=result, validation=validation)


class RecoveryOrchestrator:
    def __init__(self, solver: Callable[[str], SolverResult],
                 validator: Callable[[str, SolverResult], ValidationReport],
                 client: ExplanationClient | None = None, evidence_projector=None):
        self.solver, self.validator, self.client = solver, validator, client
        self.evidence_projector = evidence_projector

    def run_attempt(self, prepared: PreparedAttempt):
        validate_context(prepared.context)
        capability = BoundReplanningCapability(prepared, self.solver, self.validator, self.evidence_projector)
        return run_agent(ControlledTools(prepared.context, capability), self.client)


@dataclass(frozen=True)
class AgentAttemptExecution:
    outcome: object
    explanation: str
    source: str
    tool_trace: tuple[str, ...]
    structured_explanation: dict | None
    recovery_evidence: dict | None


def execute_agent_attempt(context, attempt_id, attempt_no, previous_id, fallback_summary, client=None):
    """Run the existing graph around the formal workflow's single solver call."""
    from app.integrations.agent.contracts import (
        ImpactFact, RecoveryContext as AgentContext, SolverResult as AgentSolverResult,
        ValidationReport, VerifiedSummary,
    )
    from app.modules.recovery.deterministic_orchestration import execute_recovery

    agent_context = AgentContext(
        recovery_plan_id=attempt_id,
        incident_id=context.incident_id,
        business_date=context.business_date,
        base_delivery_plan_id=context.base_plan_id,
        attempt_no=attempt_no,
        previous_recovery_plan_id=previous_id,
        incident_type=context.incident_type,
        requires_replanning=True,
        replanning_scope=context.scope.value,
        scope_description=context.scope.value,
        current_time=context.current_time,
        snapshot_token=sha256(repr(context).encode("utf-8")).hexdigest(),
        travel_time_source="GEOGRAPHIC_ESTIMATE",
        affected_orders=tuple(ImpactFact(**asdict(item)) for item in context.impact_snapshots),
        frozen_stop_ids=tuple(
            stop.id for route in context.routes for stop in route.stops
            if stop.status == "COMPLETED"
        ),
        incident_location_id=context.incident_location_id,
        delay_seconds=context.delay_seconds,
        incident_summary=context.incident_type,
        current_plan_summary={"version_no": context.base_version_no},
        available_resources=tuple(
            {"vehicle_id": str(item.vehicle_id), "driver_id": str(item.driver_id),
             "capacity_load_units": item.capacity_load_units}
            for item in context.vehicles
        ),
    )

    class Capability:
        def __init__(self):
            self.started = False
            self.outcome = None
            self.observation = None

        def solve_replanning(self):
            if self.outcome is None:
                if self.started:
                    raise RuntimeError("Solver call already started")
                self.started = True
                self.outcome = execute_recovery(context)
            result = self.outcome.solver_result
            solver = AgentSolverResult(
                status=result.status.value,
                solution_payload_json=(json.dumps(asdict(result), default=str) if result.status.value == "FEASIBLE" else None),
                diagnostic_codes=(("SOLVER_DIAGNOSTIC",) if result.diagnostic else ()),
            )
            if result.status.value != "FEASIBLE":
                self.observation = Observation(solver=solver)
                return self.observation
            issues = self.outcome.validation_issues
            validation = ValidationReport(
                status="INVALID" if issues else "VALID",
                summary=None if issues else VerifiedSummary(
                    assigned_order_count=len({stop.order_id for route in result.routes for stop in route.stops if stop.stop_type.value == "DELIVERY"}),
                    unassigned_order_ids=tuple(item.order_id for item in result.unassigned_orders),
                    changed_route_count=len(result.routes),
                    total_distance_meters=result.total_distance_meters,
                    total_duration_seconds=result.total_duration_seconds,
                ),
                diagnostic_codes=tuple(issue.code for issue in issues),
                # The canonical comparison can only be built after the candidate
                # plan has been persisted.  The formal workflow attaches it from
                # U01 inside the same transaction as candidate creation.
                recovery_evidence=None,
            )
            self.observation = Observation(solver=solver, validation=validation)
            return self.observation

    capability = Capability()
    tools = ControlledTools(agent_context, capability)
    try:
        result = run_agent(tools, None)
        return AgentAttemptExecution(
            outcome=capability.outcome,
            explanation=result.agent_explanation,
            source="template",
            tool_trace=result.tool_trace,
            structured_explanation=result.explanation.model_dump(mode="json") if result.explanation else None,
            recovery_evidence=(
                result.observation.validation.recovery_evidence.model_dump(mode="json")
                if result.observation.validation and result.observation.validation.recovery_evidence else None
            ),
        )
    except Exception:
        # A pre-solve graph failure may invoke the solver once. A solve that
        # started but never returned must not be retried; the workflow records ERROR.
        if capability.started and capability.outcome is None:
            raise
        if capability.outcome is None:
            capability.solve_replanning()
        evidence = capability.observation.validation.recovery_evidence if capability.observation and capability.observation.validation else None
        return AgentAttemptExecution(capability.outcome, fallback_summary, "template_fallback", tuple(tools.trace), None,
                                     evidence.model_dump(mode="json") if evidence else None)
