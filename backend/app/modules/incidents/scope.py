"""Only business rules own scope transitions; the Agent cannot call this module."""
from app.integrations.agent.contracts import Scope

SCOPES: tuple[Scope, ...] = ("AFFECTED_ROUTE", "CROSS_ROUTE", "ALL_REMAINING")


def next_scope(current: Scope) -> Scope | None:
    index = SCOPES.index(current)
    return SCOPES[index + 1] if index + 1 < len(SCOPES) else None


def scope_after_result(current: Scope, solver_status: str) -> Scope | None:
    return next_scope(current) if solver_status == "INFEASIBLE" else None
