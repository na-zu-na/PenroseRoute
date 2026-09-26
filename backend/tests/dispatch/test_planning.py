import json
from uuid import UUID
import pytest
from sqlalchemy import select
from app.db.models import DeliveryPlan, Order, RouteStop, DeliveryPlanOrder
from app.integrations.agent.contracts import RecoveryError, SolverResult
from app.integrations.optimization.solver import solve, validate, estimated_matrix
from app.modules.planning.service import PlanningService
from app.modules.dispatch.queries import DispatchQueries


def test_real_solver_and_normal_plan_persistence(database):
    sessions, now, day, ids = database
    service = PlanningService(sessions, clock=lambda: now)
    result = service.generate(day, "alice")
    assert result["status"] == "DRAFT" and result["matrix_source"] == "GEOGRAPHIC_ESTIMATE"
    assert result["summary"]["assigned_order_count"] == 1
    with sessions() as session:
        plan = session.get(DeliveryPlan, UUID(result["plan_id"]))
        assert plan.status == "DRAFT" and plan.validation_status == "VALID"
        stops = list(session.scalars(select(RouteStop).order_by(RouteStop.sequence_no)))
        assert [s.stop_type for s in stops] == ["PICKUP", "DELIVERY"]
        assert stops[1].precedence_stop_id == stops[0].id
        assert session.scalar(select(DeliveryPlanOrder)).order_id == ids["order"]
    with pytest.raises(RecoveryError, match="已有"):
        service.generate(day, "alice")


def test_validator_rejects_modified_solver_metrics(database):
    sessions, now, day, _ = database
    service = PlanningService(sessions, clock=lambda: now)
    with sessions() as session:
        data = service.collect(session, day, int(now.timestamp()))
    data["distance_matrix"], data["duration_matrix"] = estimated_matrix(data["locations"])
    payload = json.dumps(data)
    result = solve(payload)
    assert validate(payload, result).status == "VALID"
    output = json.loads(result.solution_payload_json)
    output["routes"][0]["distance_meters"] = 0
    changed = SolverResult(status="FEASIBLE", solution_payload_json=json.dumps(output))
    assert validate(payload, changed).status == "INVALID"


def test_stale_snapshot_writes_no_plan(database):
    sessions, now, day, ids = database
    def matrix(locations):
        with sessions() as session, session.begin():
            session.get(Order, ids["order"]).demand_load_units = 3
        return estimated_matrix(locations)
    service = PlanningService(sessions, clock=lambda: now, matrix_provider=matrix)
    with pytest.raises(RecoveryError) as exc:
        service.generate(day, "alice")
    assert exc.value.code == "PLANNING_SNAPSHOT_STALE"
    with sessions() as session:
        assert session.scalar(select(DeliveryPlan)) is None


def test_real_resource_and_risk_queries(database):
    sessions, now, day, ids = database
    query = DispatchQueries(sessions, clock=lambda: now)
    assert query.resources(day)["idle_count"] == 1
    # Order flags cannot stand in for a Current Plan or persistent U06 Alert.
    assert query.operations(day)["current_plan"] is None
    with sessions() as s, s.begin():
        s.get(Order, ids["order"]).risk_status = "AT_RISK"
    data = query.operations(day)
    assert data["current_plan"] is None
    assert data["active_alert_count"] == 0
    assert data["alert_reason_counts"] == {}
    assert "ALERT_QUERY_UNAVAILABLE" not in data["missing_reasons"]
