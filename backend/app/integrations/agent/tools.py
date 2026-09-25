from typing import Protocol
from .contracts import Observation, RecoveryContext


class ReplanningCapability(Protocol):
    """Bound application capability, backed by materialized input; no DB/session."""
    def solve_replanning(self) -> Observation: ...


class ControlledTools:
    """Only these three tools exist. No model-editable solver or scope arguments."""
    def __init__(self, context: RecoveryContext, capability: ReplanningCapability):
        self._context = context
        self._capability = capability
        self._observation = None
        self.trace: list[str] = []

    def get_recovery_context(self) -> RecoveryContext:
        self.trace.append("get_recovery_context")
        return self._context

    def solve_replanning(self) -> Observation:
        if not self.trace:
            raise RuntimeError("Context must be read before solving")
        if self._observation is None:
            self.trace.append("solve_replanning")
            self._observation = Observation.model_validate(self._capability.solve_replanning())
        return self._observation

    def get_solver_result(self) -> Observation:
        if self._observation is None:
            raise RuntimeError("No solver result yet")
        self.trace.append("get_solver_result")
        return self._observation
