from typing import TypedDict
from .contracts import RecoveryContext, Observation, AgentResult, RecoveryExplanation, VerifiedSummary, Scope


class AgentState(TypedDict, total=False):
    context: RecoveryContext
    observation: Observation
    result: AgentResult
    attempt_no: int
    replanning_scope: Scope
    solver_status: str
    validation_status: str | None
    candidate_summary: VerifiedSummary | None
    explanation: RecoveryExplanation
    diagnostic_codes: tuple[str, ...]
