from datetime import date, datetime, timezone
from uuid import uuid4
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.integrations.agent.contracts import RecoveryError
from app.integrations.dispatch_agent.contracts import DispatchCommand, DispatchContext, IntentPlan
from app.modules.dispatch.service import DispatchService
from app.modules.dispatch.session import ContextCodec

class Queries:
    def __init__(self):
        self.calls = []
        self.base, self.candidate, self.incident, self.recovery = [uuid4() for _ in range(4)]
    def operations(self, day):
        self.calls.append("operations")
        return {"facts": [{"id": "status", "text": "风险订单 2"}], "order_count": 4, "at_risk_count": 2, "open_incident_count": 2,
                "current_plan": {"plan_id": str(self.base)}}
    def resources(self, day):
        self.calls.append("resources")
        return {"total": 3, "idle_count": 1}
    def proposal(self, recovery_id):
        self.calls.append("proposal")
        assert recovery_id == self.recovery
        return {"incident_id": str(self.incident), "status": "PENDING_REVIEW",
                "base_plan": {"plan_id": str(self.base)}, "candidate_plan": {"plan_id": str(self.candidate)}}
    def compare(self, recovery_id):
        self.calls.append("compare")
        assert recovery_id == self.recovery
        return {"changed_order_count": 2, "changed_vehicle_count": 1}

@pytest.fixture
def setup():
    queries = Queries()
    return DispatchService(queries, codec=ContextCodec("s" * 32),
        clock=lambda: datetime(2026, 9, 25, 8, tzinfo=timezone.utc)), queries

def test_resource_and_risk_chain(setup):
    service, queries = setup
    result = service.run(DispatchCommand(message="看看今天迟到风险，再看看空闲车辆"), "alice")
    assert result.status == "COMPLETED"
    assert queries.calls == ["operations", "resources"]
    assert result.context.business_date == date(2026, 9, 25)
    assert "风险订单 2" in result.message

def test_clarification_retains_pending_actions(setup):
    service, queries = setup
    result = service.run(DispatchCommand(message="查看空闲车辆"), "alice")
    assert result.status == "NEEDS_INPUT" and not queries.calls
    result = service.run(DispatchCommand(message="2026-09-25", context_token=result.context_token), "alice")
    assert result.status == "COMPLETED" and queries.calls == ["resources"]

def test_proposal_then_compare_by_recovery_id(setup):
    service, queries = setup
    first = service.run(DispatchCommand(message="查看候选并比较差异"), "alice")
    assert first.status == "NEEDS_INPUT" and not queries.calls
    result = service.run(DispatchCommand(message=f"恢复方案: {queries.recovery}", context_token=first.context_token), "alice")
    assert result.status == "COMPLETED"
    assert queries.calls == ["proposal", "compare"]


def test_mixed_write_request_is_only_a_business_api_hint(setup):
    service, queries = setup
    result = service.run(DispatchCommand(message="查看今天风险和车辆，生成恢复方案"), "alice")
    assert result.status == "NEEDS_INPUT" and not result.observations and not queries.calls

@pytest.mark.parametrize("text", ["不要生成恢复方案", "如果迟到则生成恢复方案", "能否生成恢复方案", "批准方案并生成恢复方案"])
def test_no_write_for_negation_condition_or_approval(setup, text):
    service, queries = setup
    service.recovery = SimpleNamespace(run=lambda _: pytest.fail("Unexpected write"))
    result = service.run(DispatchCommand(message=text, context=DispatchContext(incident_id=queries.incident)), "alice")
    assert result.status == "NEEDS_INPUT"

def test_model_cannot_add_mutation(setup):
    service, queries = setup
    service.planner = SimpleNamespace(plan=lambda *_: IntentPlan(actions=("start_incident_recovery",)))
    service.recovery = SimpleNamespace(run=lambda _: pytest.fail("Unexpected write"))
    result = service.run(DispatchCommand(message="查看今天状态", context=DispatchContext(incident_id=queries.incident)), "alice")
    assert result.status == "COMPLETED" and result.planner_source == "fallback"
    assert queries.calls == ["operations"]

def test_model_failure_only_falls_back_to_reads(setup):
    service, queries = setup
    def fail(*_): raise TimeoutError()
    service.planner = SimpleNamespace(plan=fail)
    service.recovery = SimpleNamespace(run=lambda _: pytest.fail("Unexpected write"))
    result = service.run(DispatchCommand(message="看看今天风险", context=DispatchContext(incident_id=queries.incident)), "alice")
    assert result.planner_source == "fallback" and result.status == "COMPLETED"
    assert queries.calls == ["operations"]

def test_missing_adapter_does_not_claim_generation(setup):
    service, _ = setup
    service.planner = SimpleNamespace(plan=lambda *_: pytest.fail("Normal planning must not invoke a model"))
    result = service.run(DispatchCommand(message="生成今天配送计划"), "alice")
    assert result.status == "NEEDS_INPUT" and not result.observations
    assert "/api/planning/generate" in result.message


def test_model_cannot_introduce_normal_planning_tool(setup):
    service, _ = setup
    service.planner = SimpleNamespace(plan=lambda *_: {"actions": ["generate_delivery_plan"]})
    result = service.run(DispatchCommand(message="帮我做些安排"), "alice")
    assert result.planner_source == "fallback" and not result.observations

def test_failed_capability_stops_later_steps(setup):
    service, queries = setup
    def fail(_):
        raise RecoveryError("QUERY_UNAVAILABLE", "查询失败", 503)
    queries.operations = fail
    result = service.run(DispatchCommand(message="查看今天风险和车辆"), "alice")
    assert result.status == "FAILED" and not queries.calls and len(result.observations) == 1


def test_reader_write_request_returns_hint(setup):
    service, queries = setup
    result = service.run(DispatchCommand(message="生成恢复方案"), "reader", can_generate=False)
    assert result.status == "NEEDS_INPUT" and not queries.calls

def test_token_signature_owner_and_expiry():
    clock = [1000]
    codec = ContextCodec("secret" * 6, clock=lambda: clock[0], ttl_seconds=60)
    token = codec.encode(DispatchContext(business_date=date(2026, 9, 25)), "alice")
    assert codec.decode(token, "alice").business_date == date(2026, 9, 25)
    for bad, subject in [(token + "x", "alice"), (token, "bob")]:
        with pytest.raises(RecoveryError): codec.decode(bad, subject)
    clock[0] = 1060
    with pytest.raises(RecoveryError): codec.decode(token, "alice")

def test_new_date_clears_stale_plan_context(setup):
    service, queries = setup
    token = service.codec.encode(DispatchContext(business_date=date(2026, 9, 24),
        incident_id=queries.incident, candidate_plan_id=queries.candidate), "alice")
    result = service.run(DispatchCommand(message="看看今天车辆", context_token=token), "alice")
    assert result.context.incident_id is None and result.context.candidate_plan_id is None

def test_plan_schema_excludes_unknown_tools_and_arguments():
    with pytest.raises(ValidationError): IntentPlan(actions=("approve_plan",))
    with pytest.raises(ValidationError): IntentPlan(actions=("get_delivery_status",), arguments={"sql": "SELECT *"})


def test_bedrock_intent_schema_with_stub():
    from app.integrations.dispatch_agent.planner import BedrockIntentPlanner
    seen = []
    class Stub:
        def converse(self, **kwargs):
            seen.append(kwargs)
            return {"output": {"message": {"content": [{"toolUse": {
                "name": "plan_dispatch", "input": {"actions": ["get_resource_availability"]}}}]}}}
    result = BedrockIntentPlanner("stub", "ap-southeast-1", Stub()).plan("查询车辆", DispatchContext())
    assert result.actions == ("get_resource_availability",)
    schema = seen[0]["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert set(schema["properties"]) == {"actions", "clarification"}
