from datetime import date
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.integrations.dispatch_agent.contracts import DispatchCommand, DispatchContext
from app.modules.dispatch.service import DispatchService
from app.modules.dispatch.session import ContextCodec
from app.modules.dispatch.queries import DispatchQueries


def test_unavailable_alert_service_does_not_open_database():
    def forbidden():
        pytest.fail("Missing U06 contract must not be replaced by ORM queries")
    queries = DispatchQueries(forbidden)
    alert = queries.explain_alert(date(2026, 9, 25), order_id=uuid4())
    assert alert["alerts"] is None and alert["missing_reasons"] == ["ALERT_QUERY_UNAVAILABLE"]
    assert alert["as_of"] and not alert["truncated"]


@pytest.mark.parametrize("mode, source", [("complete", "model"), ("omit", "fallback"), ("unknown", "fallback"), ("timeout", "fallback")])
def test_summary_model_exact_permutation_and_reference_preservation(mode, source):
    data = {"as_of": "2026-09-25T08:00:00Z", "current_plan": {"id": str(uuid4())},
        "facts": [{"id": "plan", "text": "Current Plan V1"}, {"id": "missing", "text": "提醒数据不可用"}]}
    def arrange(facts):
        if mode == "timeout":
            raise TimeoutError()
        return {"fact_ids": [f.id for f in reversed(facts)] if mode == "complete" else ["bad"] if mode == "unknown" else [facts[0].id]}
    service = DispatchService(SimpleNamespace(operations=lambda _: data), codec=ContextCodec("s"*32),
                              explanation_client=SimpleNamespace(arrange=arrange))
    reply = service.run(DispatchCommand(message="2026-09-25 运营情况"), "user")
    assert reply.explanation_source == source
    assert "提醒数据不可用" in reply.message
    assert reply.observations[0].data == data
    assert bool(reply.diagnostic_codes) == (source == "fallback")


def test_date_change_clears_order_and_alert_choices_and_continuation_works():
    codec = ContextCodec("s"*32)
    service = DispatchService(DispatchQueries(None), codec=codec)
    context = DispatchContext(business_date=date(2026, 9, 24), order_id=uuid4(), alert_id=uuid4())
    first = service.run(DispatchCommand(message="2026-09-25 为什么有风险", context_token=codec.encode(context, "user")), "user")
    assert first.status == "NEEDS_INPUT"
    assert first.context.order_id is None and first.context.alert_id is None
    alert_id = uuid4()
    second = service.run(DispatchCommand(message=f"提醒: {alert_id}", context_token=first.context_token), "user")
    assert second.status == "COMPLETED" and "提醒数据不可用" in second.message
    assert second.context.alert_id == alert_id


def test_multiple_order_ids_require_clarification():
    from app.integrations.agent.contracts import RecoveryError
    service = DispatchService(None, codec=ContextCodec("s"*32))
    with pytest.raises(RecoveryError, match="多个 ID"):
        service.run(DispatchCommand(message=f"为什么订单 {uuid4()} 和订单 {uuid4()} 有风险"), "user")
