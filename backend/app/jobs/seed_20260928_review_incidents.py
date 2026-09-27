"""Seed three actionable vehicle incidents on the existing September 28 V4 plan.

The default mode only checks the target database. Run with --apply to use the
normal Stop, Incident, Recovery, and Modify workflows. It is safe to resume
after an interrupted run; it never deletes a plan or replaces the V4 Current
plan. The workflows do update route execution and vehicle availability facts.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text

from app.db.models import Incident, RecoveryPlan, VehicleDriverAssignment
from app.db.models.fleet import AssignmentStatus, ResourceStatus
from app.db.models.planning import DeliveryPlanStatus, RouteStatus, StopStatus
from app.db.models.recovery import IncidentStatus, IncidentType, RecoveryPlanStatus, ReplanningScope
from app.db.repositories.plan_repository import PlanRepository
from app.db.session import SessionLocal
from app.modules.decisions.service import DeterministicDecisionService
from app.modules.incidents.workflow import VehicleIncidentWorkflow
from app.modules.operations.execution import StopExecutionService
from app.modules.recovery.deterministic_workflow import RecoveryWorkflow


BUSINESS_DATE = date(2026, 9, 28)
SINGAPORE = timezone(timedelta(hours=8))
SOURCE_CODES = ("VEH-002", "VEH-003", "VEH-SEP28-DISPATCH")
REPLACEMENTS = (
    ("f0280000-0000-4000-8000-000000000101", "50000000-0000-0000-0000-000000000004", "60000000-0000-0000-0000-000000000004"),
    ("f0280000-0000-4000-8000-000000000102", "50000000-0000-0000-0000-000000000005", "60000000-0000-0000-0000-000000000005"),
    ("f0280000-0000-4000-8000-000000000103", "50000000-0000-0000-0000-000000000006", "60000000-0000-0000-0000-000000000006"),
)


def _routes(session):
    if session.scalar(text("select current_database()")) != "penrose_route_demo_rebuilt":
        raise RuntimeError("Refusing to seed outside penrose_route_demo_rebuilt")
    plan = PlanRepository(session).get_current_plan(BUSINESS_DATE)
    if plan is None or plan.plan_code != "PLAN-20260928-V4" or plan.status is not DeliveryPlanStatus.CURRENT:
        raise RuntimeError("PLAN-20260928-V4 is not Current")
    rows = session.execute(text("""
        select v.vehicle_code, v.id vehicle_id, r.id route_id, r.status route_status,
               first_stop.id first_stop_id, first_stop.status first_stop_status
        from vehicle_routes r
        join vehicles v on v.id = r.vehicle_id
        join route_stops first_stop on first_stop.vehicle_route_id = r.id and first_stop.sequence_no = 1
        where r.delivery_plan_id = :plan_id
        order by v.vehicle_code
    """), {"plan_id": plan.id}).mappings().all()
    if tuple(row["vehicle_code"] for row in rows) != SOURCE_CODES:
        raise RuntimeError("The V4 routed vehicles differ from the expected demo baseline")
    return plan.id, rows


def _ensure_replacements():
    with SessionLocal() as session, session.begin():
        _routes(session)
        for assignment_id, vehicle_id, driver_id in REPLACEMENTS:
            existing = session.get(VehicleDriverAssignment, UUID(assignment_id))
            if existing is not None:
                if existing.vehicle_id != UUID(vehicle_id) or existing.driver_id != UUID(driver_id):
                    raise RuntimeError("Replacement assignment identity changed")
                continue
            from app.db.models import Driver, Vehicle
            vehicle = session.get(Vehicle, UUID(vehicle_id))
            driver = session.get(Driver, UUID(driver_id))
            if vehicle is None or driver is None or vehicle.status is not ResourceStatus.AVAILABLE or driver.status is not ResourceStatus.AVAILABLE:
                raise RuntimeError("A replacement vehicle-driver pair is unavailable")
            session.add(VehicleDriverAssignment(
                id=UUID(assignment_id), vehicle_id=vehicle.id, driver_id=driver.id,
                assigned_from_at=datetime(2026, 9, 28, 16, tzinfo=SINGAPORE),
                assigned_until_at=datetime(2026, 9, 29, 0, tzinfo=SINGAPORE),
                status=AssignmentStatus.PLANNED,
            ))


def _create_incidents():
    with SessionLocal() as session:
        base_id, routes = _routes(session)
        route_facts = [(row["vehicle_code"], row["vehicle_id"], row["first_stop_id"]) for row in routes]
        session.rollback()

    incident_ids = []
    for index, (code, vehicle_id, first_stop_id) in enumerate(route_facts):
        with SessionLocal() as session:
            incident = session.scalar(select(Incident).where(
                Incident.delivery_plan_id == base_id,
                Incident.vehicle_id == vehicle_id,
                Incident.incident_type == IncidentType.VEHICLE_UNAVAILABLE,
                Incident.status != IncidentStatus.RESOLVED,
            ))
            incident_id = incident.id if incident is not None else None
            session.rollback()
        if incident_id is None:
            arrival = datetime(2026, 9, 28, 17, 50, index * 20, tzinfo=SINGAPORE)
            with SessionLocal() as session:
                StopExecutionService(session).arrive(first_stop_id, occurred_at=arrival)
            with SessionLocal() as session:
                result = VehicleIncidentWorkflow(session).report_unavailable(
                    business_date=BUSINESS_DATE, vehicle_id=vehicle_id,
                    detected_at=datetime(2026, 9, 28, 17, 55, index * 20, tzinfo=SINGAPORE),
                    location_code="LOC-MER-001", latitude=Decimal("1.332900"),
                    longitude=Decimal("103.743600"),
                )
                incident_ids.append(result.incident_id)
                print(f"Created {result.incident_code} for {code}")
        else:
            incident_ids.append(incident_id)
    return incident_ids


def _ensure_full_scope_candidate(incident_id: UUID):
    with SessionLocal() as session:
        attempts = list(session.scalars(select(RecoveryPlan).where(
            RecoveryPlan.incident_id == incident_id
        ).order_by(RecoveryPlan.attempt_no)))
        pending = next((item for item in reversed(attempts) if item.status is RecoveryPlanStatus.PENDING_REVIEW), None)
        if pending is not None:
            session.expunge(pending)
        session.rollback()
    if pending is None:
        if attempts:
            raise RuntimeError(f"Incident {incident_id} has failed attempts but no pending Candidate")
        with SessionLocal() as session:
            outcome = RecoveryWorkflow(session).start(incident_id, request_id="demo-20260928-review")
        if outcome.reviewable_recovery_plan_id is None:
            raise RuntimeError(f"Incident {incident_id} has no feasible Candidate")
        with SessionLocal() as session:
            pending = session.get(RecoveryPlan, outcome.reviewable_recovery_plan_id)
            session.expunge(pending)
            session.rollback()

    decisions = DeterministicDecisionService(SessionLocal)
    while pending.replanning_scope is not ReplanningScope.ALL_REMAINING:
        outcome = decisions.modify(
            pending.id,
            reason="Demo review must replan all remaining work after three breakdowns",
            subject="system:demo-seed", request_id="demo-20260928-review",
        )
        if outcome["outcome"] != "PENDING_REVIEW":
            raise RuntimeError(f"Incident {incident_id} Modify produced {outcome['outcome']}")
        with SessionLocal() as session:
            pending = session.get(RecoveryPlan, UUID(outcome["new_recovery_plan_id"]))
            session.expunge(pending)
            session.rollback()
    print(f"Reviewable Incident {incident_id}: Attempt {pending.attempt_no}, Candidate {pending.candidate_delivery_plan_id}")


def main(*, apply: bool):
    with SessionLocal() as session:
        _, rows = _routes(session)
        print(f"Target: V4 Current, {len(rows)} source routes, database penrose_route_demo_rebuilt")
        session.rollback()
    if not apply:
        return
    _ensure_replacements()
    incident_ids = _create_incidents()
    for incident_id in incident_ids:
        _ensure_full_scope_candidate(incident_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the demo incidents and candidates")
    main(apply=parser.parse_args().apply)
