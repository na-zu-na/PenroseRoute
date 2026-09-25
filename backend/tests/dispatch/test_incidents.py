from datetime import timedelta
from uuid import UUID
import pytest
from sqlalchemy import select
from app.api.routes.operations import IncidentCommand
from app.db.models import Incident, IncidentAffectedOrder, Order, RecoveryPlan
from app.modules.incidents.service import IncidentService
from app.modules.planning.service import PlanningService
from app.modules.recovery.application import SqlRecoveryApplication
from app.modules.recovery.workflow import RecoveryWorkflow
from app.modules.recovery.orchestration import RecoveryOrchestrator
from app.modules.recovery.evidence import project_evidence
from app.integrations.optimization.solver import solve, validate
from app.integrations.agent.contracts import RecoveryError


@pytest.mark.parametrize("delay,expected", [(600, False), (601, True)])
def test_merchant_delay_boundary_and_order_ready_time(database, delay, expected):
    sessions, now, day, ids = database
    planning = PlanningService(sessions, clock=lambda: now)
    plan = planning.generate(day, "alice")
    planning.activate(UUID(plan["plan_id"]), "alice")
    result = IncidentService(sessions, clock=lambda: now).report(IncidentCommand(
        business_date=day, incident_type="MERCHANT_DELAY", merchant_id=ids["merchant"],
        updated_ready_at=now+timedelta(seconds=delay)), "alice")
    assert result["requires_replanning"] == expected
    with sessions() as session:
        assert session.scalar(select(Incident)).status == ("DETECTED" if expected else "RESOLVED")
        assert session.scalar(select(IncidentAffectedOrder)).requires_replanning == expected
        assert session.get(Order, ids["order"]).pickup_ready_at.replace(tzinfo=now.tzinfo) == now+timedelta(seconds=delay)
    if expected:
        workflow = RecoveryWorkflow(SqlRecoveryApplication(sessions, clock=lambda: now), RecoveryOrchestrator(solve, validate, evidence_projector=project_evidence))
        reply = workflow.run(UUID(result["incident_id"]))
        assert reply.code == "RECOVERY_PENDING_REVIEW" and len(reply.data["attempts_created"]) == 1
        assert "601" in reply.data["explanation"]["summary"]
        assert reply.data["recovery_evidence"]["reassigned_orders"] == []
        assert reply.data["recovery_evidence"]["handover_order_ids"] == []
        assert reply.data["recovery_evidence"]["changed_order_ids"] == [str(ids["order"])]


def test_snapshot_failure_marks_attempt_failed_for_retry(database):
    from tests.dispatch.test_recovery_database import incident_setup
    incident, _ = incident_setup(database)
    sessions, now, day, ids = database
    application = SqlRecoveryApplication(sessions, clock=lambda: now)
    prepared = application.prepare_attempt(incident, None, None)
    result = RecoveryOrchestrator(solve, validate).run_attempt(prepared)
    with sessions() as session, session.begin():
        session.get(Order, ids["order"]).risk_status = "AT_RISK"
    with pytest.raises(RecoveryError) as exc:
        application.finish_attempt(prepared, result)
    assert exc.value.code == "RECOVERY_SNAPSHOT_STALE"
    with sessions() as session:
        attempt = session.get(RecoveryPlan, prepared.context.recovery_plan_id)
        assert attempt.solver_status == "ERROR" and attempt.candidate_delivery_plan_id is None


def test_normal_plan_activation_rejects_elapsed_start(database):
    sessions, now, day, _ = database
    planning = PlanningService(sessions, clock=lambda: now)
    plan = planning.generate(day, "alice")
    planning.clock = lambda: now+timedelta(seconds=301)
    with pytest.raises(RecoveryError) as exc:
        planning.activate(UUID(plan["plan_id"]), "alice")
    assert exc.value.code == "PLAN_SCHEDULE_STALE"
    assert planning.cancel(UUID(plan["plan_id"]))["status"] == "CANCELLED"
