"""Deterministic incident scope policy."""

from app.db.models.recovery import ReplanningScope


def initial_vehicle_unavailable_scope() -> ReplanningScope:
    return ReplanningScope.AFFECTED_ROUTE
