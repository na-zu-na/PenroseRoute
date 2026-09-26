from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.integrations.dispatch_agent.contracts import DispatchCommand, IntentPlan
from app.modules.dispatch.service import DispatchService
from app.modules.dispatch.session import ContextCodec

@pytest.mark.parametrize("action", ["start_incident_recovery", "approve", "generate_delivery_plan", "execute_sql"])
def test_no_write_intents(action):
    with pytest.raises(ValidationError):
        IntentPlan(actions=(action,))

@pytest.mark.parametrize("text", ["启动恢复", "直接批准", "如果迟到则重新规划", "不要生成计划", "执行 SQL 删除订单"])
def test_writes_never_reach_model_or_queries(text):
    service = DispatchService(None, codec=ContextCodec("s" * 32),
        planner=SimpleNamespace(plan=lambda *_: pytest.fail("write reached model")))
    reply = service.run(DispatchCommand(message=text), "reader", can_generate=False)
    assert reply.status == "NEEDS_INPUT" and not reply.observations
