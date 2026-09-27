"""One-off, guarded re-solve of the 2026-09-28 demo; dry-run by default."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import DeliveryPlan, RecoveryPlan
from app.db.models.planning import DeliveryPlanStatus
from app.db.models.recovery import DispatcherDecision, IncidentStatus, RecoveryPlanStatus
from app.db.repositories.plan_repository import PlanRepository
from app.db.session import SessionLocal
from app.integrations.optimization.contracts import SolverStatus, SolverStopType
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.optimization.result_validator import SolverResultValidator
from app.integrations.routing.distance_matrix import build_distance_time_matrix, get_routing_provider
from app.modules.operations.risk import RiskService
from app.modules.planning.finalizer import PlanFinalizer
from app.modules.planning.input_builder import build_solver_input
from app.modules.planning.workflow import PlanningWorkflow


BUSINESS_DATE = date(2026, 9, 28)
INPUT_SQL = (Path(__file__).resolve().parents[3] / "database" / "seed_20260928_ten_order_inputs.sql").read_text(encoding="utf-8")


def _check_baseline(session):
    plans = PlanRepository(session)
    base = plans.get_current_plan(BUSINESS_DATE)
    candidate = session.scalar(select(DeliveryPlan).where(DeliveryPlan.plan_code == "PLAN-20260928-V3"))
    if (base is None or base.plan_code != "PLAN-20260928-V2"
            or candidate is None or candidate.status is not DeliveryPlanStatus.CANDIDATE
            or candidate.parent_plan_id != base.id):
        raise RuntimeError("The 2026-09-28 Current/Candidate baseline changed; no data was modified")
    recovery = session.scalar(select(RecoveryPlan).where(RecoveryPlan.candidate_delivery_plan_id == candidate.id))
    if recovery is None or recovery.status is not RecoveryPlanStatus.PENDING_REVIEW:
        raise RuntimeError("The expected pending recovery is missing; no data was modified")
    return base, candidate, recovery


def _staged_facts():
    with SessionLocal() as session:
        _check_baseline(session)
        session.connection().exec_driver_sql(INPUT_SQL)
        facts = PlanningWorkflow(session)._load_facts(BUSINESS_DATE)
        PlanningWorkflow._validate_facts(facts)
        session.rollback()  # Data is never committed during the external solve.
    return facts


def main(*, apply: bool = False) -> None:
    facts = _staged_facts()
    provider = get_routing_provider()
    matrix = build_distance_time_matrix(facts.locations, provider=provider)
    solver_input = build_solver_input(facts, matrix)
    result = ORToolsSolver().solve(solver_input)
    issues = SolverResultValidator().validate(solver_input, result)
    if result.status is not SolverStatus.FEASIBLE or issues or result.unassigned_orders:
        raise RuntimeError(f"Solver did not assign all 10 orders: {result.status}, {result.unassigned_orders}, {issues}")

    risk = RiskService(threshold_seconds=get_settings().at_risk_threshold_seconds)
    orders = {order.order_id: order for order in facts.orders}
    at_risk = {
        stop.order_id for route in result.routes for stop in route.stops
        if stop.stop_type is SolverStopType.DELIVERY
        and risk.status_for_eta(
            estimated_arrival_at=facts.current_time + timedelta(seconds=stop.arrival_time_seconds),
            delivery_window_end_at=orders[stop.order_id].delivery_window_end_at,
        ).value == "AT_RISK"
    }
    if len(facts.orders) != 10 or len(result.routes) < 2 or not at_risk:
        raise RuntimeError(f"Demo requirements not met: orders={len(facts.orders)}, routes={len(result.routes)}, at_risk={len(at_risk)}")

    locations = {item.location_id: item for item in facts.locations}
    starts = {pair.vehicle_id: pair.start_location_id for pair in facts.vehicle_driver_pairs}
    build_route = getattr(provider, "build_route", None)
    road_routes = {
        route.vehicle_id: build_route((locations[starts[route.vehicle_id]], *(
            locations[stop.location_id] for stop in sorted(route.stops, key=lambda item: item.sequence_no)
        )))
        for route in result.routes
    } if build_route is not None else None
    print(f"Dry solve: {len(facts.orders)} assigned orders, {len(result.routes)} vehicle routes, {len(at_risk)} AT_RISK orders")
    if not apply:
        return

    now = datetime.now(timezone.utc)
    with SessionLocal() as session, session.begin():
        plans = PlanRepository(session)
        plans.lock_business_date_for_planning(BUSINESS_DATE)
        base, candidate, recovery = _check_baseline(session)
        session.connection().exec_driver_sql(INPUT_SQL)
        fresh_facts = PlanningWorkflow(session)._load_facts(BUSINESS_DATE)
        PlanningWorkflow._validate_facts(fresh_facts)
        if fresh_facts != facts:
            raise RuntimeError("Planning facts changed during solve; rolling back")

        candidate.status = DeliveryPlanStatus.CANCELLED
        recovery.status = RecoveryPlanStatus.DECIDED
        recovery.dispatcher_decision = DispatcherDecision.REJECT
        recovery.decision_reason = "Superseded by the expanded 10-order demo plan"
        recovery.reviewed_by = "system:demo-refresh"
        recovery.reviewed_at = now
        # The replacement current plan addresses this old-plan incident;
        # the rejected candidate itself is never applied.
        recovery.incident.status = IncidentStatus.RESOLVED
        recovery.incident.resolved_at = now
        base.status = DeliveryPlanStatus.SUPERSEDED
        base.superseded_at = now
        session.flush()  # Release the unique CURRENT index before activation.

        plan = PlanFinalizer(plans).finalize(facts, result, activated_at=now, road_routes=road_routes)
        plan.parent_plan_id = base.id  # The cancelled candidate is not the active lineage parent.
        full = plans.get_plan_with_routes(plan.id)
        for route in full.routes:
            risk.recalculate_route(stops=list(route.stops), current_time=now)
        session.flush()
        print(f"Activated {plan.plan_code} ({plan.id}); old V2/V3 snapshots retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the verified 10-order demo")
    main(apply=parser.parse_args().apply)
