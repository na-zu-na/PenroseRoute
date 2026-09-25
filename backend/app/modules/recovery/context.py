"""Defensive validation of already computed incident facts, outside the Agent."""
from app.integrations.agent.contracts import RecoveryContext, RecoveryError


def validate_context(context: RecoveryContext) -> None:
    if not context.requires_replanning:
        raise RecoveryError("RECOVERY_NOT_REQUIRED", http_status=409)
    if context.incident_type == "MERCHANT_DELAY":
        if context.delay_seconds is None:
            raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Missing deterministic delay result", 422)
        if context.delay_seconds <= 600:
            raise RecoveryError("RECOVERY_NOT_REQUIRED", http_status=409)
    if context.incident_type == "VEHICLE_UNAVAILABLE" and context.incident_location_id is None:
        raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Missing incident location", 422)
    for fact in context.affected_orders:
        completed = fact.execution_status_snapshot == "COMPLETED"
        picked = fact.execution_status_snapshot in {"PICKED_UP", "DELIVERING"}
        # Reject inconsistent rule outputs; do not repair or reclassify them in the LLM.
        if fact.was_completed != completed or (completed and (fact.requires_replanning or fact.handover_required)):
            raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Inconsistent completed snapshot", 422)
        if fact.handover_required and (not picked or not fact.requires_replanning):
            raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Inconsistent handover snapshot", 422)
        if (context.incident_type == "VEHICLE_UNAVAILABLE" and picked and fact.requires_replanning
                and not fact.handover_required):
            raise RecoveryError("RECOVERY_CONTEXT_INVALID", "Picked-up vehicle recovery needs handover", 422)
