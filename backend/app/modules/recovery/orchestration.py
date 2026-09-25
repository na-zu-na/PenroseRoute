import json
from dataclasses import dataclass
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
