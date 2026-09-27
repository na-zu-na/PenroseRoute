from datetime import date, datetime, timezone
import re
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from app.integrations.dispatch_agent.contracts import DispatchCommand, DispatchContext
from app.modules.dispatch.service import DispatchService
from app.modules.dispatch.session import ContextCodec
from app.modules.dispatch.queries import DispatchQueries


def test_missing_persisted_alert_is_explicit(database):
    from app.integrations.agent.contracts import RecoveryError

    sessions, _, day, _ = database
    queries = DispatchQueries(sessions)
    with pytest.raises(RecoveryError) as exc:
        queries.explain_alert(day, order_id=uuid4())
    assert exc.value.code == "ALERT_NOT_FOUND"
    assert "No active or historical alert" in str(exc.value)


def test_dispatch_facts_and_missing_context_are_english(database):
    sessions, now, day, _ = database
    queries = DispatchQueries(sessions, clock=lambda: now)
    facts = queries.operations(day)["facts"] + queries.resources(day)["facts"]
    assert facts
    assert all(not re.search(r"[\u3400-\u9fff]", fact["text"]) for fact in facts)
    assert all(not re.search(r"\d{4}-\d{2}-\d{2}T|[0-9a-f]{8}-[0-9a-f]{4}-", fact["text"], re.I) for fact in facts)
    reply = DispatchService(queries, codec=ContextCodec("s" * 32)).run(
        DispatchCommand(message="Show delivery status"), "reader")
    assert reply.status == "NEEDS_INPUT"
    assert "delivery date" in reply.message.lower()


def test_resource_summary_uses_natural_plural_verb(database, monkeypatch):
    from app.db.repositories.fleet_repository import FleetRepository

    monkeypatch.setattr(FleetRepository, "get_active_vehicle_driver_pairs", lambda self, at: [])
    sessions, now, day, _ = database
    prose = " ".join(fact["text"] for fact in DispatchQueries(sessions, clock=lambda: now).resources(day)["facts"])
    assert "No active vehicle-driver pairs" in prose
    assert "No pairs appear idle" in prose


def test_alert_answer_explains_risk_without_raw_evidence_or_timestamps(database):
    from datetime import timedelta
    from app.db.models.alerts import RiskAlert, RiskAlertChange
    from app.modules.planning.service import PlanningService

    sessions, now, day, ids = database
    plan_id = PlanningService(sessions, clock=lambda: now).generate(day, "dispatcher")["plan_id"]
    alert_id = uuid4()
    evidence = {
        "reason_category": "PREDICTED_MISS",
        "estimated_arrival_at": (now + timedelta(hours=9)).isoformat(),
        "delivery_window_end_at": (now + timedelta(hours=8)).isoformat(),
        "delay_seconds": 300,
        "threshold_seconds": 600,
        "vehicle_route_id": str(uuid4()),
    }
    with sessions() as session, session.begin():
        session.add(RiskAlert(id=alert_id, delivery_plan_id=UUID(plan_id),
            order_id=ids["order"], business_date=day, risk_type="DELIVERY_WINDOW",
            status="ACTIVE", evidence=evidence, detected_at=now, last_evaluated_at=now))
        session.add(RiskAlertChange(change_id=1, alert_id=alert_id, change_type="CREATED",
            recorded_at=now, evidence_snapshot=evidence))

    facts = DispatchQueries(sessions).explain_alert(day, alert_id=alert_id)["facts"]
    prose = " ".join(fact["text"] for fact in facts)
    assert "estimated arrival" in prose.lower()
    assert "delivery window" in prose.lower()
    assert not re.search(r"\d{4}-\d{2}-\d{2}T|[0-9a-f]{8}-[0-9a-f]{4}-|[{}]|PREDICTED_MISS", prose, re.I)


@pytest.mark.parametrize("incidents,reviews,at_risk,expected", [
    (1, 1, 1, ("1 order remains unfinished", "1 order may miss its delivery window", "1 incident still needs attention", "1 recovery option awaits dispatcher review")),
    (0, 0, 0, ("1 order remains unfinished", "No orders are currently at risk", "No unresolved incidents need attention", "no recovery options need review")),
])
def test_operations_summary_uses_natural_counts(database, monkeypatch, incidents, reviews, at_risk, expected):
    from dataclasses import dataclass
    from app.modules.operations.queries import OperationsQueryService

    @dataclass
    class Snapshot:
        current_plan: dict
        orders: dict
        calculated_at: str
        open_incidents: int
        pending_recovery_reviews: int

    monkeypatch.setattr(OperationsQueryService, "dashboard", lambda self, day: Snapshot(
        current_plan={"delivery_plan_id": str(uuid4()), "version_no": 1},
        orders={"total": 2, "completed": 1, "at_risk": at_risk},
        calculated_at="2026-09-25T00:00:00Z", open_incidents=incidents,
        pending_recovery_reviews=reviews,
    ))
    sessions, now, day, _ = database
    prose = " ".join(fact["text"] for fact in DispatchQueries(sessions, clock=lambda: now).operations(day)["facts"])
    assert all(phrase in prose for phrase in expected)


def test_comparison_describes_one_frozen_order_naturally(database, monkeypatch):
    from dataclasses import dataclass
    from app.modules.planning.comparison import PlanComparisonService

    @dataclass
    class Metrics:
        delta_distance_meters: int | None = None
        delta_duration_seconds: int | None = None

    @dataclass
    class Stop:
        order_id: UUID
        stop_type: str = "HANDOVER"
        change_type: str = "ADDED"

    @dataclass
    class Comparison:
        recovery_plan_id: UUID
        candidate_plan_id: UUID
        business_date: date
        comparison_at: datetime
        reassigned_order_count: int
        frozen_completed_order_ids: tuple[UUID, ...]
        stop_changes: tuple
        orders: tuple
        remaining_metrics: Metrics
        reviewable: bool

    recovery_id = uuid4()
    monkeypatch.setattr(PlanComparisonService, "compare_recovery", lambda self, _: Comparison(
        recovery_id, uuid4(), date(2026, 9, 25), datetime(2026, 9, 25, tzinfo=timezone.utc),
        0, (uuid4(),), (Stop(uuid4()),), (), Metrics(), True,
    ))
    sessions, _, _, _ = database
    prose = " ".join(fact["text"] for fact in DispatchQueries(sessions).compare(recovery_id)["facts"])
    assert "1 completed order remains protected" in prose
    assert "1 order requires a cargo handover" in prose


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
    queries = SimpleNamespace(explain_alert=lambda *_: {
        "alerts": [{"id": "persisted"}],
        "facts": [{"id": "alert", "text": "已读取持久化提醒"}],
    })
    service = DispatchService(queries, codec=codec)
    context = DispatchContext(business_date=date(2026, 9, 24), order_id=uuid4(), alert_id=uuid4())
    first = service.run(DispatchCommand(message="2026-09-25 为什么有风险", context_token=codec.encode(context, "user")), "user")
    assert first.status == "NEEDS_INPUT"
    assert first.context.order_id is None and first.context.alert_id is None
    alert_id = uuid4()
    second = service.run(DispatchCommand(message=f"提醒: {alert_id}", context_token=first.context_token), "user")
    assert second.status == "COMPLETED" and "已读取持久化提醒" in second.message
    assert second.context.alert_id == alert_id


def test_multiple_order_ids_require_clarification():
    from app.integrations.agent.contracts import RecoveryError
    service = DispatchService(None, codec=ContextCodec("s"*32))
    with pytest.raises(RecoveryError, match="Multiple IDs"):
        service.run(DispatchCommand(message=f"为什么订单 {uuid4()} 和订单 {uuid4()} 有风险"), "user")
