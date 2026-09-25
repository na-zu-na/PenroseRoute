"""Read all Recovery Attempts, including failed attempts without candidates."""
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.recovery_repository import RecoveryRepository


class RecoveryQueryService:
    def __init__(self, session: Session) -> None:
        self.incidents = IncidentRepository(session)
        self.recoveries = RecoveryRepository(session)

    def list_attempts(self, incident_id: UUID) -> list[dict]:
        if self.incidents.get_incident_for_query(incident_id) is None:
            raise NotFound(code="INCIDENT_NOT_FOUND", message="Incident was not found")
        return [self._detail(attempt) for attempt in self.recoveries.get_recovery_attempts(incident_id)]

    def get_attempt(self, recovery_plan_id: UUID) -> dict:
        attempt = self.recoveries.get_recovery_plan_by_id(recovery_plan_id)
        if attempt is None:
            raise NotFound(code="RECOVERY_NOT_FOUND", message="Recovery attempt was not found")
        return self._detail(attempt)

    @staticmethod
    def _detail(attempt) -> dict:
        return {
            "recovery_plan_id": attempt.id,
            "recovery_code": attempt.recovery_code,
            "incident_id": attempt.incident_id,
            "attempt_no": attempt.attempt_no,
            "previous_recovery_plan_id": attempt.previous_recovery_plan_id,
            "base_delivery_plan_id": attempt.base_delivery_plan_id,
            "base_plan_code": attempt.base_delivery_plan.plan_code,
            "candidate_delivery_plan_id": attempt.candidate_delivery_plan_id,
            "candidate_plan_code": (
                attempt.candidate_delivery_plan.plan_code
                if attempt.candidate_delivery_plan is not None else None
            ),
            "status": attempt.status.value,
            "replanning_scope": attempt.replanning_scope.value,
            "scope_description": attempt.scope_description,
            "agent_explanation": attempt.agent_explanation,
            "solver_status": attempt.solver_status.value if attempt.solver_status else None,
            "validation_status": attempt.validation_status.value if attempt.validation_status else None,
            "solver_validation_summary": attempt.solver_validation_summary,
            "dispatcher_decision": (
                attempt.dispatcher_decision.value if attempt.dispatcher_decision else None
            ),
            "decision_reason": attempt.decision_reason,
            "reviewed_by": attempt.reviewed_by,
            "reviewed_at": attempt.reviewed_at,
            "created_at": attempt.created_at,
            "updated_at": attempt.updated_at,
        }
