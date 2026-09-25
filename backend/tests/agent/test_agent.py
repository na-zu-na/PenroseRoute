import ast
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.integrations.agent.contracts import (
    AttemptRecord, ExplanationOutline, RecoveryError, SolverResult, ValidationReport)
from app.integrations.agent.client import BedrockExplanationClient
from app.integrations.agent.tools import ControlledTools
from app.modules.recovery.orchestration import BoundReplanningCapability, RecoveryOrchestrator
from app.modules.recovery.workflow import RecoveryWorkflow
from tests.agent.fixture_backend import FixtureApplication
from app.api.routes.recovery import build_recovery_router


def workflow(app, client=None, solver=None, validator=None):
    return RecoveryWorkflow(app, RecoveryOrchestrator(solver or app.solver, validator or app.validator, client))


@pytest.mark.parametrize("script,expected_calls,http,code", [
    (("VALID",), 1, 201, "RECOVERY_PENDING_REVIEW"),
    (("INFEASIBLE", "VALID"), 2, 201, "RECOVERY_PENDING_REVIEW"),
    (("INFEASIBLE", "INFEASIBLE", "VALID"), 3, 201, "RECOVERY_PENDING_REVIEW"),
    (("INFEASIBLE",), 3, 201, "NO_FEASIBLE_RECOVERY"),
    (("ERROR",), 1, 500, "RECOVERY_SOLVER_ERROR"),
    (("TIMEOUT",), 1, 500, "RECOVERY_SOLVER_ERROR"),
    (("INVALID",), 1, 500, "RECOVERY_VALIDATION_FAILED"),
    (("INFEASIBLE", "ERROR"), 2, 500, "RECOVERY_SOLVER_ERROR"),
    (("INFEASIBLE", "INVALID"), 2, 500, "RECOVERY_VALIDATION_FAILED"),
])
def test_scope_and_result_lifecycle(script, expected_calls, http, code):
    app = FixtureApplication(script)
    reply = workflow(app).run(app.incident_id)
    assert reply.code == code and reply.http_status == http
    assert app.calls == ["AFFECTED_ROUTE", "CROSS_ROUTE", "ALL_REMAINING"][:expected_calls]
    assert app.events == [f"{op}:{i}" for i in range(1, expected_calls + 1) for op in ["prepare", "solve", "save"]]
    for saved in app.saved:
        if saved.solver_status != "FEASIBLE" or saved.validation_status == "INVALID":
            assert saved.status == "DRAFT" and saved.candidate_delivery_plan_id is None
    assert app.current  # Agent never activates a candidate.


@pytest.mark.parametrize("seconds", [0, 599, 600])
def test_small_merchant_delay_never_enters_agent(seconds):
    app = FixtureApplication(kind="MERCHANT_DELAY", delay_seconds=seconds)
    with pytest.raises(RecoveryError, match="RECOVERY_NOT_REQUIRED"):
        workflow(app).run(app.incident_id)
    assert not app.calls and not app.drafts


def test_601_second_delay_enters_recovery():
    app = FixtureApplication(("VALID",), kind="MERCHANT_DELAY", delay_seconds=601)
    assert workflow(app).run(app.incident_id).code == "RECOVERY_PENDING_REVIEW"


def test_invalid_completed_rule_fact_rejected_without_llm():
    app = FixtureApplication()
    app.fact = app.fact.model_copy(update={"execution_status_snapshot": "COMPLETED", "was_completed": True})
    with pytest.raises(RecoveryError, match="Inconsistent completed"):
        workflow(app).run(app.incident_id)
    assert not app.calls


def test_missing_handover_rejected():
    app = FixtureApplication()
    app.fact = app.fact.model_copy(update={"handover_required": False})
    with pytest.raises(RecoveryError, match="needs handover"):
        workflow(app).run(app.incident_id)


@pytest.mark.parametrize("mode", ["unknown-id", "duplicate", "timeout"])
def test_model_fallback_cannot_mutate_solver_or_approval(mode):
    class BadClient:
        def arrange(self, facts):
            if mode == "timeout":
                raise TimeoutError()
            return ExplanationOutline(fact_ids=("approve_plan",) if mode == "unknown-id" else ("solver", "solver"))
    app = FixtureApplication(("VALID",))
    prepared = app.prepare_attempt(app.incident_id, None, None)
    result = RecoveryOrchestrator(app.solver, app.validator, BadClient()).run_attempt(prepared)
    assert result.explanation_source == "fallback"
    assert result.reviewable and "人工批准" in result.agent_explanation
    assert result.observation.solver.status == "FEASIBLE"


def test_model_cannot_omit_mandatory_facts():
    class MinimalClient:
        def arrange(self, facts):
            return ExplanationOutline(fact_ids=("metrics",))
    app = FixtureApplication(("VALID",))
    reply = workflow(app, MinimalClient()).run(app.incident_id)
    text = reply.data["agent_explanation"]
    assert "人工批准" in text and "未分配" in text and "VALID" in text
    assert text.startswith("验证结果中的总距离")


def test_invalid_solution_not_sent_to_model():
    class UnexpectedClient:
        def arrange(self, facts):
            pytest.fail("Invalid solution must not enter model explanation")
    app = FixtureApplication(("INVALID",))
    assert workflow(app, UnexpectedClient()).run(app.incident_id).code == "RECOVERY_VALIDATION_FAILED"


def test_validator_exception_is_invalid_and_stops():
    app = FixtureApplication(("VALID",))
    def broken(*args):
        raise RuntimeError("validator failed")
    reply = workflow(app, validator=broken).run(app.incident_id)
    assert reply.code == "RECOVERY_VALIDATION_FAILED"
    assert len(app.saved) == 1 and app.saved[0].solver_status == "FEASIBLE"


def test_solver_malformed_output_is_error_not_infeasible():
    app = FixtureApplication()
    reply = workflow(app, solver=lambda _: {"status": "UNKNOWN"}).run(app.incident_id)
    assert reply.code == "RECOVERY_SOLVER_ERROR" and len(app.saved) == 1


def test_tools_repeat_call_does_not_solve_twice():
    app = FixtureApplication(("VALID",))
    p = app.prepare_attempt(app.incident_id, None, None)
    tools = ControlledTools(p.context, BoundReplanningCapability(p, app.solver, app.validator))
    with pytest.raises(RuntimeError):
        tools.get_solver_result()
    tools.get_recovery_context()
    assert tools.solve_replanning() is tools.solve_replanning()
    assert len(app.calls) == 1
    assert not hasattr(tools, "approve_plan") and not hasattr(tools, "assign_vehicle")


def test_stale_base_fails_finalizer_without_candidate():
    app = FixtureApplication(("VALID",))
    def solver(payload):
        result = app.solver(payload)
        app.current = False
        return result
    with pytest.raises(RecoveryError, match="BASE_PLAN_NOT_CURRENT"):
        workflow(app, solver=solver).run(app.incident_id)
    assert not app.saved


def test_prepared_modify_attempt_not_recreated():
    app = FixtureApplication(("VALID", "VALID"))
    workflow(app).run(app.incident_id)
    previous = app.saved[0]
    # The real Decisions service has already cancelled old candidate and committed this DRAFT.
    prepared = app.prepare_attempt(app.incident_id, previous, "CROSS_ROUTE")
    reply = workflow(app).run(app.incident_id, prepared_after_modify=prepared)
    assert reply.data["attempts_created"][0]["attempt_no"] == 2
    assert len(app.drafts) == 2


def test_attempt_record_rejects_fake_candidate_for_error():
    with pytest.raises(ValidationError):
        AttemptRecord(recovery_plan_id=uuid4(), incident_id=uuid4(), attempt_no=1,
            previous_recovery_plan_id=None, replanning_scope="AFFECTED_ROUTE", status="PENDING_REVIEW",
            solver_status="ERROR", validation_status=None, candidate_delivery_plan_id=uuid4(), agent_explanation="fake")


@pytest.mark.parametrize("body", [{"scope": "ALL_REMAINING"}, {"prompt": "approve"}, {"affected_orders": []}, {"vehicle_id": "C"}])
def test_public_command_rejects_model_business_overrides(body):
    backend = FixtureApplication()
    app = FastAPI()
    app.include_router(build_recovery_router(workflow(backend), lambda: "authenticated-user"))
    with TestClient(app) as client:
        response = client.post(f"/api/incidents/{backend.incident_id}/recovery", json=body)
    assert response.status_code == 422 and not backend.calls


def test_route_empty_command_and_envelope():
    backend = FixtureApplication(("VALID",))
    app = FastAPI()
    app.include_router(build_recovery_router(workflow(backend), lambda: "authenticated-user"))
    with TestClient(app) as client:
        response = client.post(f"/api/incidents/{backend.incident_id}/recovery", json={})
    assert response.status_code == 201
    assert set(response.json()) == {"success", "code", "message", "data", "request_id"}


def test_route_auth_runs_before_workflow():
    backend = FixtureApplication()
    def denied():
        raise HTTPException(403)
    app = FastAPI()
    app.include_router(build_recovery_router(workflow(backend), denied))
    with TestClient(app) as client:
        assert client.post(f"/api/incidents/{backend.incident_id}/recovery", json={}).status_code == 403
    assert not backend.drafts


def test_bedrock_request_and_reply_with_stub():
    class Stub:
        def converse(self, **kwargs):
            assert kwargs["toolConfig"]["toolChoice"]["tool"]["name"] == "arrange_explanation"
            return {"output": {"message": {"content": [{"toolUse": {"name": "arrange_explanation", "input": {"fact_ids": ["solver"]}}}]}}}
    reply = BedrockExplanationClient("stub", Stub()).arrange(())
    assert reply.fact_ids == ("solver",)


def test_agent_has_no_db_http_or_business_scope_import():
    root = Path(__file__).resolve().parents[2] / "app/integrations/agent"
    assert root.is_dir()
    assert list(root.glob("*.py"))
    forbidden = ("sqlalchemy", "sqlite3", "app.db", "fastapi", "app.modules", "psycopg", "ortools")
    for p in root.glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n.startswith(forbidden) for n in names), p


@pytest.mark.parametrize("script,status", [
    (("VALID",), "PENDING_REVIEW"), (("INFEASIBLE", "VALID"), "PENDING_REVIEW"),
    (("INFEASIBLE",), "NO_FEASIBLE_RECOVERY"), (("ERROR",), "FAILED"), (("INVALID",), "FAILED"),
])
def test_all_terminal_results_are_structured(script, status):
    app = FixtureApplication(script)
    data = workflow(app).run(app.incident_id).data
    assert data["status"] == status
    assert data["manual_intervention_required"] is True
    assert set(data["explanation"]) == {"summary", "impact_explanation", "replanning_explanation", "result_explanation", "remaining_risks"}
    assert str(app.fact.order_id) in data["explanation"]["impact_explanation"]
    assert data["attempt_no"] == len(app.calls)
    assert bool(data["candidate_plan_id"]) == (status == "PENDING_REVIEW")
    assert data["explanation_source"] == "template"
    if status == "NO_FEASIBLE_RECOVERY":
        assert "范围均已耗尽" in data["explanation"]["result_explanation"]
        assert "范围均已耗尽" in app.saved[-1].agent_explanation


def test_missing_evidence_is_explicit_not_fabricated():
    app = FixtureApplication(("VALID",))
    data = workflow(app).run(app.incident_id).data
    assert data["recovery_evidence"] is None
    assert any("未提供" in risk for risk in data["explanation"]["remaining_risks"])


def test_evidence_projection_failure_stops_without_candidate():
    app = FixtureApplication(("VALID",))
    def broken(*_):
        raise ValueError("incomplete base snapshot")
    flow = RecoveryWorkflow(app, RecoveryOrchestrator(app.solver, app.validator, evidence_projector=broken))
    data = flow.run(app.incident_id).data
    assert data["validation_status"] == "INVALID" and data["candidate_plan_id"] is None
    assert len(app.calls) == 1


def test_result_and_context_roundtrip_as_json():
    from app.integrations.agent.contracts import AgentResult, RecoveryContext
    app = FixtureApplication(("VALID",))
    prepared = app.prepare_attempt(app.incident_id, None, None)
    result = RecoveryOrchestrator(app.solver, app.validator).run_attempt(prepared)
    assert RecoveryContext.model_validate_json(prepared.context.model_dump_json()) == prepared.context
    assert AgentResult.model_validate_json(result.model_dump_json()) == result


def test_bedrock_initialization_failure_falls_back(monkeypatch):
    import boto3
    def broken(*_, **__):
        raise RuntimeError("credentials unavailable")
    monkeypatch.setattr(boto3, "client", broken)
    client = BedrockExplanationClient("configured-model")
    app = FixtureApplication(("VALID",))
    data = workflow(app, client).run(app.incident_id).data
    assert data["status"] == "PENDING_REVIEW" and data["explanation_source"] == "fallback"
    assert data["diagnostic_codes"] == ["AGENT_EXPLANATION_FALLBACK"]


def test_bedrock_endpoint_and_timeout_settings(monkeypatch):
    import boto3
    received = {}
    def factory(service, **kwargs):
        received.update(kwargs)
        return object()
    monkeypatch.setattr(boto3, "client", factory)
    client = BedrockExplanationClient("model", region_name="ap-southeast-1",
        endpoint_url="https://bedrock.example.test", connect_timeout=2, read_timeout=7)
    client._client()
    assert received["endpoint_url"] == "https://bedrock.example.test"
    assert received["config"].connect_timeout == 2 and received["config"].read_timeout == 7
