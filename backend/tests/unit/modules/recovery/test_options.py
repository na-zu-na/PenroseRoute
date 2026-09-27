from dataclasses import replace
from uuid import UUID

from tests.unit.integrations.optimization.test_ortools_solver import (
    solver_input, order, vehicle, HUB, PICKUP_A, DELIVERY_A, ORDER_A, VEHICLE_A, VEHICLE_B,
)


def data(vehicles=3):
    ids = [VEHICLE_A, VEHICLE_B, UUID(int=12345)][:vehicles]
    return solver_input(
        orders=(order(ORDER_A, PICKUP_A, DELIVERY_A),),
        vehicles=tuple(vehicle(v, HUB) for v in ids),
        location_ids=(HUB, PICKUP_A, DELIVERY_A),
        duration_matrix=((0, 20, 40), (20, 0, 30), (40, 30, 0)),
    )


def test_generates_distinct_valid_resource_alternatives():
    from app.modules.recovery.options import solve_alternatives
    from app.integrations.optimization.result_validator import SolverResultValidator
    source = data()
    alternatives = solve_alternatives(source, max_candidates=3)
    assert len(alternatives) == 3
    assert len({tuple(r.vehicle_id for r in a.routes) for a in alternatives}) == 3
    assert all(not a.unassigned_orders for a in alternatives)
    assert all(not SolverResultValidator().validate(source, a) for a in alternatives)


def test_does_not_fabricate_options_when_only_one_vehicle_available():
    from app.modules.recovery.options import solve_alternatives
    assert len(solve_alternatives(data(1), max_candidates=3)) == 1


def test_infeasible_options_never_enter_review():
    from app.modules.recovery.options import solve_alternatives
    source = data()
    source = replace(source, orders=(replace(source.orders[0], demand_load_units=100),))
    assert solve_alternatives(source, max_candidates=3) == ()


def test_priority_prefers_coverage_then_less_disruption_before_speed():
    from app.modules.recovery.options import priority_key
    slow = dict(unassigned_order_count=0, reassigned_order_count=1,
                changed_order_count=2, handover_count=1, completion_at='2026-09-25T05:00:00+00:00')
    fast = dict(slow, reassigned_order_count=2, completion_at='2026-09-25T04:00:00+00:00')
    missing = dict(slow, unassigned_order_count=1, reassigned_order_count=0)
    assert priority_key(slow) < priority_key(fast) < priority_key(missing)


def test_onboard_vehicle_is_never_removed_to_create_an_alternative():
    from app.modules.recovery.options import solve_alternatives
    source = data()
    onboard = replace(source.orders[0], pickup_location_id=None, required_vehicle_id=VEHICLE_A,
                      pickup_service_seconds=0)
    source = replace(source, orders=(onboard,), vehicles=tuple(
        replace(v, initial_load_load_units=1) if v.vehicle_id == VEHICLE_A else v
        for v in source.vehicles))
    choices = solve_alternatives(source, max_candidates=3)
    assert len(choices) == 1
    assert choices[0].routes[0].vehicle_id == VEHICLE_A
