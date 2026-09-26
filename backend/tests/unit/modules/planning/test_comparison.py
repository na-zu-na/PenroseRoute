"""A copied plan is compared by business actions, not database row IDs."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID


BASE = UUID("80000000-0000-0000-0000-000000000001")
CANDIDATE = UUID("80000000-0000-0000-0000-000000000002")
ORDER = UUID("40000000-0000-0000-0000-000000000001")
VEHICLE = UUID("50000000-0000-0000-0000-000000000001")
LOCATION = UUID("10000000-0000-0000-0000-000000000002")
AT = datetime(2026, 9, 25, 10, 5, tzinfo=timezone.utc)


def _stop(stop_id: int, kind: str, sequence: int, *, completed: bool = False):
    return SimpleNamespace(
        id=UUID(int=stop_id), order_id=ORDER, stop_type=kind,
        sequence_no=sequence, location_id=LOCATION, source_incident_id=None,
        precedence_stop_id=None, planned_arrival_at=AT,
        planned_departure_at=AT, status="COMPLETED" if completed else "PLANNED",
        actual_arrival_at=AT if completed else None,
        actual_departure_at=AT if completed else None,
    )


def _plan(plan_id: UUID, *, stop_id: int = 1, completed: bool = False):
    route_id = UUID(int=stop_id + 100)
    pickup = _stop(stop_id, "PICKUP", 1, completed=completed)
    delivery = _stop(stop_id + 1, "DELIVERY", 2, completed=completed)
    delivery.precedence_stop_id = pickup.id
    route = SimpleNamespace(
        id=route_id, vehicle_id=VEHICLE, route_no=1,
        stops=[pickup, delivery],
    )
    membership = SimpleNamespace(
        order_id=ORDER, assignment_status="ASSIGNED",
        vehicle_route_id=route_id, unassigned_reason_code=None,
        unassigned_reason_detail=None,
    )
    return SimpleNamespace(
        id=plan_id, plan_code=f"PLAN-{plan_id.int}",
        business_date=date(2026, 9, 25), status="CANDIDATE",
        routes=[route], plan_orders=[membership],
    )


def test_copied_stops_with_new_ids_are_not_business_changes():
    from app.modules.planning.comparison import compare_plan_snapshots

    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=_plan(BASE, stop_id=1), candidate=_plan(CANDIDATE, stop_id=10),
        reviewable=True,
    )

    assert result.stop_changes == ()
    assert result.orders[0].assignment_changed is False
    assert result.orders[0].route_task_changed is False
    assert result.route_changes == ()
    assert result.remaining_metrics.base_distance_meters is None
    assert result.remaining_metrics.reason == "NO_COMPARABLE_REMAINDER_SNAPSHOT"


def test_completed_facts_are_reported_as_frozen_when_preserved():
    from app.modules.planning.comparison import compare_plan_snapshots

    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=_plan(BASE, stop_id=1, completed=True),
        candidate=_plan(CANDIDATE, stop_id=10, completed=True),
        reviewable=True,
    )

    assert result.frozen_completed_order_ids == (ORDER,)
    assert result.orders[0].eta_delta_seconds is None
    assert result.orders[0].eta_unavailable_reason == "COMPLETED_DELIVERY"


def test_completed_order_is_not_marked_frozen_if_its_pickup_fact_changed():
    from app.modules.planning.comparison import compare_plan_snapshots

    candidate = _plan(CANDIDATE, stop_id=10, completed=True)
    candidate.routes[0].stops[0].actual_arrival_at = datetime(
        2026, 9, 25, 10, 6, tzinfo=timezone.utc
    )
    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=_plan(BASE, stop_id=1, completed=True),
        candidate=candidate, reviewable=True,
    )

    assert result.frozen_completed_order_ids == ()
    assert any(item.stop_type == "PICKUP" for item in result.stop_changes)


def test_sequence_change_on_same_vehicle_is_a_route_task_change():
    from app.modules.planning.comparison import compare_plan_snapshots

    base = _plan(BASE)
    candidate = _plan(CANDIDATE, stop_id=10)
    candidate.routes[0].stops[0].sequence_no = 2
    candidate.routes[0].stops[1].sequence_no = 1

    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=base, candidate=candidate, reviewable=True,
    )

    assert {item.stop_type for item in result.stop_changes} == {"PICKUP", "DELIVERY"}
    assert result.orders[0].route_task_changed is True
    assert result.route_changes[0].vehicle_id == VEHICLE


def test_unassigned_membership_controls_vehicle_and_eta_even_if_stop_remains():
    from app.modules.planning.comparison import compare_plan_snapshots

    base = _plan(BASE)
    candidate = _plan(CANDIDATE, stop_id=10)
    candidate.plan_orders[0].assignment_status = "UNASSIGNED"
    candidate.plan_orders[0].vehicle_route_id = None
    candidate.plan_orders[0].unassigned_reason_code = "NO_FEASIBLE_ROUTE"

    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=base, candidate=candidate, reviewable=True,
    )

    order = result.orders[0]
    assert order.assignment_changed is True
    assert order.candidate_assignment_status == "UNASSIGNED"
    assert order.candidate_vehicle_id is None
    assert order.candidate_unassigned_reason_code == "NO_FEASIBLE_ROUTE"
    assert order.base_delivery_eta == AT
    assert order.candidate_delivery_eta is None
    assert order.eta_unavailable_reason == "DELIVERY_STOP_NOT_AVAILABLE"
    assert result.reassigned_order_count == 0


def test_changed_stop_time_exposes_both_stored_times():
    from app.modules.planning.comparison import compare_plan_snapshots

    candidate = _plan(CANDIDATE, stop_id=10)
    candidate.routes[0].stops[0].planned_arrival_at = AT + timedelta(minutes=5)
    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=_plan(BASE), candidate=candidate, reviewable=True,
    )

    pickup = next(item for item in result.stop_changes if item.stop_type == "PICKUP")
    assert pickup.base_planned_arrival_at == AT
    assert pickup.candidate_planned_arrival_at == AT + timedelta(minutes=5)


def test_changed_delivery_predecessor_exposes_business_action_not_stop_id():
    from app.modules.planning.comparison import compare_plan_snapshots

    candidate = _plan(CANDIDATE, stop_id=10)
    handover = _stop(99, "HANDOVER", 2)
    handover.source_incident_id = UUID(int=55)
    candidate.routes[0].stops.append(handover)
    candidate.routes[0].stops[1].sequence_no = 3
    candidate.routes[0].stops[1].precedence_stop_id = handover.id
    result = compare_plan_snapshots(
        recovery_plan_id=UUID(int=999), comparison_at=AT,
        base=_plan(BASE), candidate=candidate, reviewable=True,
    )

    delivery = next(item for item in result.stop_changes if item.stop_type == "DELIVERY")
    assert delivery.base_precedence_stop_type == "PICKUP"
    assert delivery.candidate_precedence_stop_type == "HANDOVER"
    assert delivery.candidate_precedence_location_id == LOCATION
