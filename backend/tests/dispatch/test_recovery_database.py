from datetime import timedelta
from uuid import UUID, uuid4
from sqlalchemy import select
from app.db.models import DeliveryPlan, Incident, IncidentAffectedOrder, Order, RecoveryPlan, Vehicle, Driver, VehicleDriverAssignment, VehicleRoute, RouteStop
from app.modules.planning.service import PlanningService
from app.modules.recovery.application import SqlRecoveryApplication
from app.modules.recovery.orchestration import RecoveryOrchestrator
from app.modules.recovery.workflow import RecoveryWorkflow
from app.modules.recovery.evidence import project_evidence
from app.integrations.optimization.solver import solve, validate
from app.modules.dispatch.queries import DispatchQueries


def incident_setup(database, picked=False):
    sessions, now, day, ids = database
    initial = PlanningService(sessions, clock=lambda: now).generate(day, "dispatcher")
    incident_id, v2, d2, a2 = [uuid4() for _ in range(4)]
    with sessions() as session, session.begin():
        plan = session.get(DeliveryPlan, UUID(initial["plan_id"]))
        plan.status, plan.activated_at = "CURRENT", now
        route = session.scalar(select(VehicleRoute).where(VehicleRoute.delivery_plan_id == plan.id))
        if picked:
            order = session.get(Order, ids["order"])
            order.execution_status = "PICKED_UP"
            stop = session.scalar(select(RouteStop).where(RouteStop.vehicle_route_id == route.id, RouteStop.stop_type == "PICKUP"))
            stop.status, stop.actual_arrival_at, stop.actual_departure_at = "COMPLETED", now, now+timedelta(seconds=30)
        session.get(Vehicle, ids["vehicle"]).status = "UNAVAILABLE"
        session.add(Vehicle(id=v2, vehicle_code="V2", name="Backup", capacity_load_units=5, status="AVAILABLE",
                            current_location_id=ids["pickup"], current_location_recorded_at=now))
        session.add(Driver(id=d2, driver_code="D2", name="Backup", status="AVAILABLE"))
        session.add(VehicleDriverAssignment(id=a2, vehicle_id=v2, driver_id=d2, status="ACTIVE", activated_at=now,
            assigned_from_at=now, assigned_until_at=now+timedelta(hours=12)))
        session.add(Incident(id=incident_id, incident_code="INC1", incident_type="VEHICLE_UNAVAILABLE", status="DETECTED",
            delivery_plan_id=plan.id, vehicle_route_id=route.id, vehicle_id=ids["vehicle"],
            incident_location_id=ids["pickup"], detected_at=now, detected_by="dispatcher"))
        session.add(IncidentAffectedOrder(id=uuid4(), incident_id=incident_id, order_id=ids["order"], original_vehicle_route_id=route.id,
            execution_status_snapshot="PICKED_UP" if picked else "PLANNED", risk_status_snapshot="NORMAL", was_picked_up=picked,
            was_completed=False, requires_replanning=True, handover_required=picked,
            impact_type="HANDOVER_REQUIRED" if picked else "PICKUP_REPLAN", impact_reason="Vehicle unavailable", assessed_at=now))
    return incident_id, UUID(initial["plan_id"])


def run_recovery(database, picked=False):
    incident, base = incident_setup(database, picked)
    sessions, now, day, ids = database
    workflow = RecoveryWorkflow(SqlRecoveryApplication(sessions, clock=lambda: now+timedelta(seconds=60)),
                                RecoveryOrchestrator(solve, validate, evidence_projector=project_evidence))
    return workflow.run(incident), base, incident


def test_real_recovery_persists_attempts_and_full_candidate(database):
    reply, base_id, incident = run_recovery(database)
    assert reply.code == "RECOVERY_PENDING_REVIEW"
    assert len(reply.data["attempts_created"]) == 2
    sessions, now, day, ids = database
    with sessions() as session:
        assert session.get(DeliveryPlan, base_id).status == "CURRENT"
        candidate = session.get(DeliveryPlan, UUID(reply.data["candidate_delivery_plan_id"]))
        assert candidate.status == "CANDIDATE" and candidate.parent_plan_id == base_id
        attempts = list(session.scalars(select(RecoveryPlan).order_by(RecoveryPlan.attempt_no)))
        assert [a.solver_status for a in attempts] == ["INFEASIBLE", "FEASIBLE"]
        assert session.get(Incident, incident).status == "REVIEW"
    queries = DispatchQueries(sessions, clock=lambda: now)
    proposal = queries.proposal(UUID(reply.data["reviewable_recovery_plan_id"]))
    assert proposal["requires_human_review"]
    difference = queries.compare(base_id, UUID(reply.data["candidate_delivery_plan_id"]))
    assert difference["changed_order_count"] == 1


def test_handover_preserves_completed_pickup(database):
    reply, base, _ = run_recovery(database, picked=True)
    sessions, *_ = database
    with sessions() as session:
        candidate_id = UUID(reply.data["candidate_delivery_plan_id"])
        stops = list(session.scalars(select(RouteStop).join(VehicleRoute).where(VehicleRoute.delivery_plan_id == candidate_id)))
        assert sorted(s.stop_type for s in stops) == ["DELIVERY", "HANDOVER", "PICKUP"]
        pickup = next(s for s in stops if s.stop_type == "PICKUP")
        handover = next(s for s in stops if s.stop_type == "HANDOVER")
        delivery = next(s for s in stops if s.stop_type == "DELIVERY")
        assert pickup.status == "COMPLETED" and pickup.actual_departure_at is not None
        assert handover.source_incident_id is not None
        assert delivery.precedence_stop_id == handover.id
        evidence = reply.data["recovery_evidence"]
        assert evidence["handover_order_ids"] == [str(delivery.order_id)]
        assert any(str(delivery.order_id) in risk and "交接" in risk for risk in evidence["remaining_risks"])
        candidate = session.get(DeliveryPlan, candidate_id)
        for field, value in evidence["after"].items():
            assert getattr(candidate, field) == value


def test_context_contains_materialized_plan_resources_and_history(database):
    incident, base_id = incident_setup(database)
    sessions, now, *_ = database
    application = SqlRecoveryApplication(sessions, clock=lambda: now)
    first = application.prepare_attempt(incident, None, None)
    assert first.context.current_plan_summary["id"] == str(base_id)
    assert first.context.incident_summary and not first.context.previous_attempts
    assert first.context.incident_facts["incident_type"] == "VEHICLE_UNAVAILABLE"
    result = RecoveryOrchestrator(solve, validate, evidence_projector=project_evidence).run_attempt(first)
    saved = application.finish_attempt(first, result)
    second = application.prepare_attempt(incident, saved, "CROSS_ROUTE")
    assert second.context.previous_attempts[0]["solver_status"] == "INFEASIBLE"
    assert second.context.available_resources[0]["capacity"] > 0
    assert second.context.available_resources[0]["location_id"]


def test_human_approve_switches_versions_atomically(database):
    from app.modules.decisions.service import DecisionService
    from app.integrations.agent.contracts import RecoveryError
    import pytest
    reply, base, incident = run_recovery(database)
    sessions, now, *_ = database
    recovery = UUID(reply.data["reviewable_recovery_plan_id"])
    result = DecisionService(sessions, clock=lambda: now+timedelta(seconds=90)).decide(recovery, "APPROVE", "确认交接", "alice")
    assert result["candidate_status"] == "CURRENT"
    with sessions() as s:
        assert s.get(DeliveryPlan, base).status == "SUPERSEDED"
        assert s.get(Incident, incident).status == "RESOLVED"
        assert s.get(RecoveryPlan, recovery).reviewed_by == "alice"
    with pytest.raises(RecoveryError):
        DecisionService(sessions, clock=lambda: now+timedelta(seconds=90)).decide(recovery, "APPROVE", "再试", "alice")


def test_reject_keeps_current_plan(database):
    from app.modules.decisions.service import DecisionService
    reply, base, incident = run_recovery(database)
    sessions, now, *_ = database
    result = DecisionService(sessions, clock=lambda: now+timedelta(seconds=90)).decide(
        UUID(reply.data["reviewable_recovery_plan_id"]), "REJECT", "需要重评估", "alice")
    assert result["candidate_status"] == "CANCELLED" and result["current_plan_id"] == str(base)


def test_stale_candidate_cannot_be_approved(database):
    from app.modules.decisions.service import DecisionService
    from app.integrations.agent.contracts import RecoveryError
    import pytest
    reply, base, _ = run_recovery(database)
    sessions, now, day, ids = database
    with sessions() as s, s.begin():
        s.get(Order, ids["order"]).risk_status = "AT_RISK"
    with pytest.raises(RecoveryError) as exc:
        DecisionService(sessions, clock=lambda: now+timedelta(seconds=90)).decide(
            UUID(reply.data["reviewable_recovery_plan_id"]), "APPROVE", "确认", "alice")
    assert exc.value.code == "RECOVERY_SNAPSHOT_STALE"
    with sessions() as s:
        assert s.get(DeliveryPlan, base).status == "CURRENT"


def test_modify_creates_exactly_one_next_attempt(database):
    from app.modules.decisions.service import DecisionService
    reply, base, incident = run_recovery(database)
    sessions, now, *_ = database
    workflow = RecoveryWorkflow(SqlRecoveryApplication(sessions, clock=lambda: now+timedelta(seconds=100)), RecoveryOrchestrator(solve, validate))
    modified = DecisionService(sessions, clock=lambda: now+timedelta(seconds=100)).modify(
        UUID(reply.data["reviewable_recovery_plan_id"]), "扩大范围评估", "alice", workflow)
    assert modified.code == "RECOVERY_PENDING_REVIEW"
    with sessions() as s:
        attempts = list(s.scalars(select(RecoveryPlan).order_by(RecoveryPlan.attempt_no)))
        assert len(attempts) == 3
        assert attempts[1].dispatcher_decision == "MODIFY"
        assert attempts[2].replanning_scope == "ALL_REMAINING"
        assert attempts[2].previous_recovery_plan_id == attempts[1].id
        assert s.get(DeliveryPlan, attempts[1].candidate_delivery_plan_id).status == "CANCELLED"
    import pytest
    from app.integrations.agent.contracts import RecoveryError
    with pytest.raises(RecoveryError) as exc:
        DecisionService(sessions, clock=lambda: now+timedelta(seconds=120)).modify(
            UUID(modified.data["reviewable_recovery_plan_id"]), "继续扩大", "alice", workflow)
    assert exc.value.code == "RECOVERY_SCOPE_EXHAUSTED"
    with sessions() as s:
        assert s.get(RecoveryPlan, UUID(modified.data["reviewable_recovery_plan_id"])).status == "PENDING_REVIEW"
