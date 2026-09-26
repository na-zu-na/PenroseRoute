"""Formal P0 recovery with the real graph, tools, solver, and PostgreSQL."""

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import DeliveryPlan, Incident, Order, RecoveryPlan, RouteStop, Vehicle, VehicleRoute
from app.db.models.fleet import ResourceStatus
from app.db.models.planning import DeliveryPlanStatus, StopStatus, StopType
from app.db.models.resources import OrderExecutionStatus
from app.core.config import Settings
from app.integrations.agent.contracts import ExplanationOutline
from app.integrations.agent.tools import ControlledTools
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.optimization.contracts import SolverResult, SolverStatus
from app.integrations.optimization.result_validator import SolverResultValidator, ValidationIssue
from app.modules.recovery import deterministic_context
from tests.integration.test_p0_non_agent_e2e import _post_ok, demo_client, seed_demo


class RecordingExplanationClient:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
        self.facts = []

    def arrange(self, facts):
        self.calls += 1
        self.facts.append(facts)
        if self.fail:
            raise TimeoutError("model unavailable")
        return ExplanationOutline(fact_ids=tuple(fact.id for fact in facts))


def test_missing_model_id_uses_template_in_agent_mode(monkeypatch):
    from app.api import dependencies

    settings = Settings(_env_file=None, agent_explanation_provider="bedrock", bedrock_model_id=None)
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)
    assert dependencies.get_recovery_explanation_client(mode="agent") is None


def test_formal_recovery_runs_agent_graph_tools_real_solver_and_approval(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.modules.recovery.deterministic_workflow")
    business_date = date(2026, 11, 2)
    clock = {"now": datetime(2026, 11, 2, 9, 10, tzinfo=timezone.utc)}
    monkeypatch.setattr(deterministic_context, "_now", lambda: clock["now"])
    model = RecordingExplanationClient()
    tool_calls = []
    original_context = ControlledTools.get_recovery_context
    original_solve = ControlledTools.solve_replanning
    original_result = ControlledTools.get_solver_result

    def read_tool(self):
        tool_calls.append("get_recovery_context")
        return original_context(self)

    def solve_tool(self):
        tool_calls.append("solve_replanning")
        return original_solve(self)

    def result_tool(self):
        tool_calls.append("get_solver_result")
        return original_result(self)

    monkeypatch.setattr(ControlledTools, "get_recovery_context", read_tool)
    monkeypatch.setattr(ControlledTools, "solve_replanning", solve_tool)
    monkeypatch.setattr(ControlledTools, "get_solver_result", result_tool)
    solver_calls = []
    original_solver = ORToolsSolver.solve

    def solve(self, solver_input):
        if solver_input.recovery_scope is not None:
            solver_calls.append(solver_input.recovery_scope.value)
        return original_solver(self, solver_input)

    monkeypatch.setattr(ORToolsSolver, "solve", solve)

    with demo_client(lambda: clock["now"] + timedelta(minutes=1), recovery_mode="agent", explanation_client=model) as (client, sessions):
        ids = seed_demo(sessions, business_date)
        base_id = _post_ok(client, "/api/planning/generate", {"business_date": business_date.isoformat()}, 201, "PLAN_CREATED")["delivery_plan_id"]
        with sessions() as session:
            route = session.scalar(select(VehicleRoute).where(VehicleRoute.delivery_plan_id == base_id))
            stops = list(session.scalars(select(RouteStop).where(RouteStop.vehicle_route_id == route.id).order_by(RouteStop.sequence_no)))
            stop_data = [(stop.id, stop.planned_arrival_at) for stop in stops[:3]]
        last_event = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
        for stop_id, planned_arrival in stop_data:
            arrived = max(last_event + timedelta(seconds=1), planned_arrival)
            for action, event in (("arrive", arrived), ("start-service", arrived + timedelta(seconds=1)), ("complete", arrived + timedelta(seconds=61))):
                _post_ok(client, f"/api/route-stops/{stop_id}/{action}", {"occurred_at": event.isoformat()})
            last_event = arrived + timedelta(seconds=61)
        clock["now"] = last_event + timedelta(minutes=2)
        with sessions() as session, session.begin():
            session.get(Vehicle, ids["spare"]).status = ResourceStatus.AVAILABLE
        incident = _post_ok(client, "/api/incidents/vehicle-unavailable", {
            "business_date": business_date.isoformat(), "vehicle_id": str(ids["primary"]),
            "location": {"location_code": f"AGENT-BREAK-{uuid4().hex[:8]}", "address_text": "Breakdown", "latitude": 1.3015, "longitude": 103.8015},
            "detected_at": clock["now"].isoformat(),
        }, 201, "VEHICLE_INCIDENT_CREATED")
        incident_id = incident["incident_id"]
        response = client.post(f"/api/incidents/{incident_id}/recovery", json={}, headers={"X-Request-ID": "agent-e2e-breakdown"})
        assert response.status_code == 201, response.text
        assert response.json()["code"] == "RECOVERY_PENDING_REVIEW"
        data = response.json()["data"]
        candidate_id = data["candidate_delivery_plan_id"]
        assert [item["solver_status"] for item in data["attempts_created"]] == ["INFEASIBLE", "FEASIBLE"]
        assert tool_calls == ["get_recovery_context", "solve_replanning", "get_solver_result"] * 2
        assert solver_calls == ["AFFECTED_ROUTE", "CROSS_ROUTE"]
        assert model.calls == 1
        texts = "\n".join(f.text for f in model.facts[0])
        assert "NO_COMPARABLE_REMAINDER_SNAPSHOT" in texts
        assert "ETA" in texts and "Handover" in texts and "Completed Freeze" in texts
        assert "候选未分配" in texts and "订单改派" in texts
        assert "本次重新求解的路线" not in texts
        assert any(
            f"request_id=agent-e2e-breakdown incident_id={incident_id}" in record.message
            and "attempt_no=2 mode=agent" in record.message
            for record in caplog.records
        )
        with sessions() as session:
            attempts = list(session.scalars(select(RecoveryPlan).where(RecoveryPlan.incident_id == incident_id).order_by(RecoveryPlan.attempt_no)))
            assert attempts[1].solver_validation_summary["explanation_source"] == "agent"
            assert attempts[1].solver_validation_summary["tool_trace"] == ["get_recovery_context", "solve_replanning", "get_solver_result"]
            assert {
                (item["order_id"], item["from_vehicle_id"], item["to_vehicle_id"])
                for item in attempts[1].solver_validation_summary["recovery_evidence"]["reassigned_orders"]
            } == {
                (str(order_id), str(ids["primary"]), str(ids["spare"]))
                for order_id in ids["orders"][1:]
            }
            assert str(ids["orders"][1]) in attempts[1].agent_explanation
            evidence = attempts[1].solver_validation_summary["recovery_evidence"]
            assert evidence["source"] == "P1_PLAN_COMPARISON"
            assert evidence["comparison_time_basis"] == "ATTEMPT_RECORDED_AT"
            assert evidence["reviewable"] is True
            assert evidence["remaining_metrics"] == {
                "base_distance_meters": None,
                "candidate_distance_meters": None,
                "delta_distance_meters": None,
                "base_duration_seconds": None,
                "candidate_duration_seconds": None,
                "delta_duration_seconds": None,
                "reason": "NO_COMPARABLE_REMAINDER_SNAPSHOT",
            }
            evidence_after = evidence["after"]
            candidate = session.get(DeliveryPlan, candidate_id)
            assert evidence_after["assigned_order_count"] == candidate.assigned_order_count
            assert evidence_after["unassigned_order_count"] == candidate.unassigned_order_count
            assert evidence_after["vehicle_count"] is None
            assert evidence_after["total_distance_meters"] is None
            assert evidence_after["total_duration_seconds"] is None
            candidate_stops = list(session.scalars(select(RouteStop).join(VehicleRoute).where(VehicleRoute.delivery_plan_id == candidate_id)))
            handover = next(stop for stop in candidate_stops if stop.stop_type is StopType.HANDOVER)
            delivery = next(stop for stop in candidate_stops if stop.order_id == ids["orders"][1] and stop.stop_type is StopType.DELIVERY)
            assert handover.location_id == session.get(Incident, incident_id).incident_location_id
            assert session.get(VehicleRoute, handover.vehicle_route_id).vehicle_id == ids["spare"]
            assert delivery.precedence_stop_id == handover.id
            assert any(stop.status is StopStatus.COMPLETED and stop.order_id == ids["orders"][0] for stop in candidate_stops)
            assert session.get(Order, ids["orders"][0]).execution_status is OrderExecutionStatus.COMPLETED
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT
            assert session.get(DeliveryPlan, candidate_id).status is DeliveryPlanStatus.CANDIDATE
        _post_ok(client, f"/api/recovery-plans/{data['reviewable_recovery_plan_id']}/approve", {"decision_reason": "Accept handover"}, code="RECOVERY_APPROVED")
        with sessions() as session:
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.SUPERSEDED
            assert session.get(DeliveryPlan, candidate_id).status is DeliveryPlanStatus.CURRENT
            assert session.get(Incident, incident_id).status.value == "RESOLVED"


def merchant_incident(client, sessions, business_date):
    day = datetime.combine(business_date, datetime.min.time(), timezone.utc)
    ids = seed_demo(sessions, business_date)
    base_id = _post_ok(client, "/api/planning/generate", {"business_date": business_date.isoformat()}, 201, "PLAN_CREATED")["delivery_plan_id"]
    incident = _post_ok(client, "/api/incidents/merchant-delay", {
        "business_date": business_date.isoformat(), "merchant_id": str(ids["merchants"][0]),
        "updated_ready_at": (day + timedelta(hours=8, minutes=15)).isoformat(),
        "detected_at": (day + timedelta(hours=8, minutes=5)).isoformat(),
    }, 201, "MERCHANT_DELAY_ASSESSED")
    assert incident["delay_seconds"] == 900
    return incident["incident_id"], base_id


def test_agent_merchant_delay_model_failure_falls_back_and_modify_uses_same_graph(monkeypatch):
    business_date = date(2026, 11, 3)
    now = datetime(2026, 11, 3, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(deterministic_context, "_now", lambda: now)
    model = RecordingExplanationClient(fail=True)
    solve_scopes = []
    original_solve = ORToolsSolver.solve

    def solve(self, solver_input):
        if solver_input.recovery_scope is not None:
            solve_scopes.append(solver_input.recovery_scope.value)
        return original_solve(self, solver_input)

    monkeypatch.setattr(ORToolsSolver, "solve", solve)
    with demo_client(lambda: now + timedelta(minutes=1), recovery_mode="agent", explanation_client=model) as (client, sessions):
        incident_id, base_id = merchant_incident(client, sessions, business_date)
        first = _post_ok(client, f"/api/incidents/{incident_id}/recovery", {}, 201, "RECOVERY_PENDING_REVIEW")
        first_candidate = first["candidate_delivery_plan_id"]
        with sessions() as session:
            recovery = session.get(RecoveryPlan, first["reviewable_recovery_plan_id"])
            assert recovery.solver_validation_summary["explanation_source"] == "template_fallback"
            assert "AGENT_EXPLANATION_FALLBACK" not in recovery.agent_explanation
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT
            candidate = session.get(DeliveryPlan, first_candidate)
            assert candidate.status is DeliveryPlanStatus.CANDIDATE
            evidence = recovery.solver_validation_summary["recovery_evidence"]
            assert evidence["source"] == "P1_PLAN_COMPARISON"
            assert evidence["remaining_metrics"]["reason"] == "NO_COMPARABLE_REMAINDER_SNAPSHOT"
            assert evidence["after"]["total_distance_meters"] is None
        modified = _post_ok(client, f"/api/recovery-plans/{first['reviewable_recovery_plan_id']}/modify", {
            "decision_reason": "Retry wider scope",
        }, code="RECOVERY_MODIFICATION_PROCESSED")
        assert modified["status"] == "PENDING_REVIEW"
        assert modified["replanning_scope"] == "CROSS_ROUTE"
        assert model.calls == 2
        assert solve_scopes == ["AFFECTED_ROUTE", "CROSS_ROUTE"]
        with sessions() as session:
            first_attempt = session.get(RecoveryPlan, first["reviewable_recovery_plan_id"])
            next_attempt = session.get(RecoveryPlan, modified["new_recovery_plan_id"])
            assert first_attempt.dispatcher_decision.value == "MODIFY"
            assert session.get(DeliveryPlan, first_candidate).status is DeliveryPlanStatus.CANCELLED
            assert next_attempt.previous_recovery_plan_id == first_attempt.id
            assert next_attempt.solver_validation_summary["explanation_source"] == "template_fallback"
            assert next_attempt.solver_validation_summary["tool_trace"] == ["get_recovery_context", "solve_replanning", "get_solver_result"]
            evidence = next_attempt.solver_validation_summary["recovery_evidence"]
            assert evidence["source"] == "P1_PLAN_COMPARISON"
            assert evidence["after"]["vehicle_count"] is None
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT


@pytest.mark.parametrize("failure_kind, expected_code", [
    ("ERROR", "RECOVERY_SOLVER_ERROR"),
    ("INVALID", "RECOVERY_VALIDATION_FAILED"),
])
def test_agent_failure_attempt_stops_without_scope_expansion(monkeypatch, failure_kind, expected_code):
    business_date = date(2026, 11, 4 if failure_kind == "ERROR" else 5)
    now = datetime.combine(business_date, datetime.min.time(), timezone.utc) + timedelta(hours=8, minutes=5)
    monkeypatch.setattr(deterministic_context, "_now", lambda: now)
    solver_calls = []
    real_solve = ORToolsSolver.solve

    def solve(self, solver_input):
        result = real_solve(self, solver_input)
        if solver_input.recovery_scope is None:
            return result
        solver_calls.append(solver_input.recovery_scope.value)
        if failure_kind == "ERROR":
            assert result.status is SolverStatus.FEASIBLE
            return SolverResult(SolverStatus.ERROR, (), (), 0, 0, "Injected solver error")
        return result

    monkeypatch.setattr(ORToolsSolver, "solve", solve)
    model = RecordingExplanationClient()
    with demo_client(lambda: now, recovery_mode="agent", explanation_client=model) as (client, sessions):
        incident_id, base_id = merchant_incident(client, sessions, business_date)
        if failure_kind == "INVALID":
            monkeypatch.setattr(SolverResultValidator, "validate", lambda *_: (ValidationIssue("INJECTED_INVALID", "Injected validation failure"),))
        response = client.post(f"/api/incidents/{incident_id}/recovery", json={})
        assert response.status_code == 500, response.text
        assert response.json()["code"] == expected_code
        assert response.json()["data"]["candidate_delivery_plan_id"] is None
        assert response.json()["data"]["manual_intervention_required"] is True
        assert solver_calls == ["AFFECTED_ROUTE"]
        assert model.calls == 0
        with sessions() as session:
            attempts = list(session.scalars(select(RecoveryPlan).where(RecoveryPlan.incident_id == incident_id)))
            assert len(attempts) == 1
            assert attempts[0].candidate_delivery_plan_id is None
            assert attempts[0].replanning_scope.value == "AFFECTED_ROUTE"
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT


@pytest.mark.parametrize("failure_stage", ["before_solve", "after_solve"])
def test_agent_graph_failure_fallback_never_repeats_solver(monkeypatch, failure_stage):
    from app.modules.recovery import orchestration

    business_date = date(2026, 11, 6 if failure_stage == "before_solve" else 7)
    now = datetime.combine(business_date, datetime.min.time(), timezone.utc) + timedelta(hours=8, minutes=5)
    monkeypatch.setattr(deterministic_context, "_now", lambda: now)
    scopes = []
    real_solve = ORToolsSolver.solve

    def solve(self, solver_input):
        if solver_input.recovery_scope is not None:
            scopes.append(solver_input.recovery_scope.value)
        return real_solve(self, solver_input)

    def broken_graph(tools, client):
        if failure_stage == "after_solve":
            tools.get_recovery_context()
            tools.solve_replanning()
        raise RuntimeError("Injected graph failure")

    monkeypatch.setattr(ORToolsSolver, "solve", solve)
    monkeypatch.setattr(orchestration, "run_agent", broken_graph)
    with demo_client(lambda: now, recovery_mode="agent") as (client, sessions):
        incident_id, _ = merchant_incident(client, sessions, business_date)
        data = _post_ok(client, f"/api/incidents/{incident_id}/recovery", {}, 201, "RECOVERY_PENDING_REVIEW")
        assert scopes == ["AFFECTED_ROUTE"]
        with sessions() as session:
            attempt = session.get(RecoveryPlan, data["reviewable_recovery_plan_id"])
            assert attempt.solver_validation_summary["explanation_source"] == "template"


@pytest.mark.parametrize("mode, expected_source", [
    ("success", "agent"), ("timeout", "template_fallback"),
    ("omitted", "template_fallback"), ("persist_failure", "template"),
    ("no_model", "template"), ("stale_candidate", "template"),
    ("already_rejected", "template"),
])
def test_postcommit_explanation_preserves_single_candidate(monkeypatch, mode, expected_source):
    from sqlalchemy import func
    from sqlalchemy.orm import Session
    from app.api.routes.recovery import get_recovery_workflow
    from app.main import app
    from app.modules.recovery.deterministic_workflow import RecoveryWorkflow
    from app.modules.decisions.service import DeterministicDecisionService

    day = date(2026, 12, 7)
    now = datetime(2026, 12, 7, 8, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(deterministic_context, "_now", lambda: now)
    solves = []
    original_solve = ORToolsSolver.solve
    def counted_solve(self, data):
        if data.recovery_scope is not None:
            solves.append(data.recovery_scope)
        return original_solve(self, data)
    monkeypatch.setattr(ORToolsSolver, "solve", counted_solve)
    holder = {}
    original_flush = Session.flush
    def flush(self, *args, **kwargs):
        if mode == "persist_failure" and any(
            isinstance(obj, RecoveryPlan) and
            (obj.solver_validation_summary or {}).get("explanation_source") == "agent"
            for obj in self.dirty
        ):
            raise RuntimeError("injected explanation persistence failure")
        return original_flush(self, *args, **kwargs)
    monkeypatch.setattr(Session, "flush", flush)

    class Model:
        calls = 0
        def arrange(self, facts):
            self.calls += 1
            # No database transaction or lock held by the workflow across transport.
            assert not holder["workflow"].session.in_transaction()
            with sessions() as session:
                attempt = session.scalar(select(RecoveryPlan).where(RecoveryPlan.incident_id == holder["incident"]))
                assert attempt.status.value == "PENDING_REVIEW"
                assert attempt.agent_explanation
                assert attempt.solver_validation_summary["explanation_source"] == "template"
                attempt_id, candidate_id = attempt.id, attempt.candidate_delivery_plan_id
            assert {fact.id for fact in facts} == {"summary", "impact", "replanning", "result", "risks", "review", "travel_source", "provenance"}
            assert "NO_COMPARABLE_REMAINDER_SNAPSHOT" in "".join(f.text for f in facts)
            if mode == "timeout":
                raise TimeoutError()
            if mode == "omitted":
                return {"fact_ids": ["result"]}
            if mode == "stale_candidate":
                with sessions() as session, session.begin():
                    stop = session.scalar(select(RouteStop).join(VehicleRoute).where(VehicleRoute.delivery_plan_id == candidate_id))
                    stop.planned_arrival_at += timedelta(seconds=1)
            if mode == "already_rejected":
                DeterministicDecisionService(sessions, clock=lambda: now).decide(attempt_id, "REJECT", "review during model", "reviewer")
            return {"fact_ids": [fact.id for fact in reversed(facts)]}

    model = Model()
    with demo_client(lambda: now, recovery_mode="agent", explanation_client=model) as (client, sessions):
        incident_id, base_id = merchant_incident(client, sessions, day)
        from uuid import UUID
        holder["incident"] = UUID(incident_id)
        def workflow_dependency():
            with sessions() as session:
                workflow = RecoveryWorkflow(session, mode="agent", explanation_client=None if mode == "no_model" else model)
                holder["workflow"] = workflow
                yield workflow
        app.dependency_overrides[get_recovery_workflow] = workflow_dependency
        response = _post_ok(client, f"/api/incidents/{incident_id}/recovery", {}, 201, "RECOVERY_PENDING_REVIEW")
        with sessions() as session:
            attempts = list(session.scalars(select(RecoveryPlan).where(RecoveryPlan.incident_id == holder["incident"])))
            assert len(attempts) == 1 and len(solves) == 1
            attempt = attempts[0]
            assert session.scalar(select(func.count()).select_from(DeliveryPlan).where(DeliveryPlan.parent_plan_id == base_id)) == 1
            assert attempt.solver_validation_summary["explanation_source"] == expected_source
            assert response["agent_explanation"] == attempt.agent_explanation
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT
            if mode == "already_rejected":
                assert attempt.status.value == "DECIDED"
                assert attempt.dispatcher_decision.value == "REJECT"
            else:
                assert attempt.status.value == "PENDING_REVIEW"
                assert session.get(DeliveryPlan, attempt.candidate_delivery_plan_id).status is DeliveryPlanStatus.CANDIDATE
        assert model.calls == (0 if mode == "no_model" else 1)
