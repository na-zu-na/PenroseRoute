from dataclasses import dataclass
from typing import Protocol
from uuid import UUID
from app.integrations.agent.contracts import AgentResult, AttemptRecord, RecoveryError, Scope
from app.modules.incidents.scope import scope_after_result
from .orchestration import PreparedAttempt, RecoveryOrchestrator


class RecoveryApplication(Protocol):
    """Implemented by existing application services, NEVER passed into Agent tools."""
    def prepare_attempt(self, incident_id: UUID, previous: AttemptRecord | None,
                        next_scope: Scope | None) -> PreparedAttempt:
        """Short transaction A: auth/context checks, materialize DTO/input, DRAFT, commit.

        Must reject parallel attempts/review, stale base; maintain attempt chain.
        For first attempt, incidents/scope supplies initial scope; next_scope=None.
        Return with all read/write transactions and ORM lazy access closed.
        """
        ...

    def finish_attempt(self, prepared: PreparedAttempt, result: AgentResult) -> AttemptRecord:
        """Short transaction B: recheck base CURRENT and snapshot freshness.

        Persist failure or atomically finalize full-fleet candidate and membership,
        routes/stops, recovery PENDING_REVIEW + incident REVIEW. Never auto-apply.
        Failed attempts remain DRAFT and do not consume a plan version.
        """
        ...


@dataclass(frozen=True)
class WorkflowReply:
    http_status: int
    success: bool
    code: str
    message: str
    data: dict

    def envelope(self, request_id: str):
        return {"success": self.success, "code": self.code, "message": self.message,
                "data": self.data, "request_id": request_id}


class RecoveryWorkflow:
    def __init__(self, application: RecoveryApplication, orchestrator: RecoveryOrchestrator):
        self.application, self.orchestrator = application, orchestrator

    def run(self, incident_id: UUID, *, prepared_after_modify: PreparedAttempt | None = None):
        """P0 command has no prompt/scope fields. Modify passes its already committed DRAFT."""
        history: list[AttemptRecord] = []
        prepared = prepared_after_modify or self.application.prepare_attempt(incident_id, None, None)
        while True:
            context = prepared.context
            if context.incident_id != incident_id:
                raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Incident identity mismatch", 422)
            result = self.orchestrator.run_attempt(prepared)  # No transaction active here.
            # Only the business policy can declare scope exhaustion. Persist this
            # terminal explanation with the final failed attempt as well.
            if result.observation.solver.status == "INFEASIBLE" and scope_after_result(context.replanning_scope, "INFEASIBLE") is None:
                notice = "所有允许的重规划范围均已耗尽，未生成可行候选，需要人工处理。"
                explanation = result.explanation.model_copy(update={
                    "summary": result.explanation.summary + "\n" + notice,
                    "result_explanation": notice,
                    "remaining_risks": result.explanation.remaining_risks + (notice,),
                }) if result.explanation else None
                result = result.model_copy(update={"explanation": explanation,
                    "agent_explanation": result.agent_explanation + "\n" + notice})
            saved = self.application.finish_attempt(prepared, result)
            self._check_saved(context, result, saved)
            history.append(saved)
            projection = saved.model_dump(mode="json", exclude={"incident_id", "previous_recovery_plan_id", "agent_explanation"})
            details = {
                "recovery_plan_id": str(saved.recovery_plan_id), "attempt_no": saved.attempt_no,
                "solver_status": saved.solver_status, "validation_status": saved.validation_status,
                "candidate_plan_id": str(saved.candidate_delivery_plan_id) if saved.candidate_delivery_plan_id else None,
                "agent_explanation": result.agent_explanation,
                "explanation": result.explanation.model_dump(mode="json") if result.explanation else None,
                "explanation_source": result.explanation_source, "tool_trace": list(result.tool_trace),
                "diagnostic_codes": list(result.observation.solver.diagnostic_codes)
                    + list(result.observation.validation.diagnostic_codes if result.observation.validation else ())
                    + list(result.diagnostic_codes),
                "recovery_evidence": result.observation.validation.recovery_evidence.model_dump(mode="json")
                    if result.observation.validation and result.observation.validation.recovery_evidence else None,
            }
            if saved.status == "PENDING_REVIEW":
                return WorkflowReply(201, True, "RECOVERY_PENDING_REVIEW",
                    "A valid recovery candidate is ready for dispatcher review", {
                        **details, "status": "PENDING_REVIEW", "manual_intervention_required": True,
                        "incident_id": str(incident_id), "outcome": "PENDING_REVIEW",
                        "attempts_created": [r.model_dump(mode="json", exclude={"incident_id", "previous_recovery_plan_id", "agent_explanation"}) for r in history],
                        "reviewable_recovery_plan_id": str(saved.recovery_plan_id),
                        "candidate_delivery_plan_id": str(saved.candidate_delivery_plan_id),
                        "agent_explanation": saved.agent_explanation})
            if saved.solver_status == "ERROR" or saved.validation_status == "INVALID":
                code = "RECOVERY_SOLVER_ERROR" if saved.solver_status == "ERROR" else "RECOVERY_VALIDATION_FAILED"
                return WorkflowReply(500, False, code, "Recovery failed; automatic scope expansion stopped",
                                     {**projection, **details, "status": "FAILED", "manual_intervention_required": True,
                                      "attempts_created": [r.model_dump(mode="json") for r in history]})
            following = scope_after_result(saved.replanning_scope, saved.solver_status)
            if following is None:
                return WorkflowReply(201, True, "NO_FEASIBLE_RECOVERY", "No feasible recovery in permitted scopes", {
                    **details, "status": "NO_FEASIBLE_RECOVERY",
                    "incident_id": str(incident_id), "outcome": "NO_FEASIBLE_RECOVERY",
                    "attempts_created": [r.model_dump(mode="json", exclude={"incident_id", "previous_recovery_plan_id", "agent_explanation"}) for r in history],
                    "reviewable_recovery_plan_id": None, "candidate_delivery_plan_id": None,
                    "manual_intervention_required": True})
            # Persist failed attempt BEFORE opening a new short transaction for next scope.
            prepared = self.application.prepare_attempt(incident_id, saved, following)
            nxt = prepared.context
            if (nxt.replanning_scope != following or nxt.attempt_no != saved.attempt_no + 1
                    or nxt.previous_recovery_plan_id != saved.recovery_plan_id
                    or nxt.base_delivery_plan_id != context.base_delivery_plan_id
                    or nxt.business_date != context.business_date):
                raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Invalid deterministic attempt transition", 422)

    @staticmethod
    def _check_saved(context, result, saved):
        observed = result.observation
        expected_validation = observed.validation.status if observed.validation else None
        if (saved.recovery_plan_id != context.recovery_plan_id or saved.incident_id != context.incident_id
                or saved.attempt_no != context.attempt_no or saved.previous_recovery_plan_id != context.previous_recovery_plan_id
                or saved.replanning_scope != context.replanning_scope or saved.solver_status != observed.solver.status
                or saved.validation_status != expected_validation or saved.agent_explanation != result.agent_explanation):
            raise RecoveryError("RECOVERY_EXECUTION_ERROR", "Application finalizer violated result contract")
