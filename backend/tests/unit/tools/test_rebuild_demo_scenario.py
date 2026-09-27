import pytest

from tools.rebuild_demo_scenario import choose_breakdown_prefix


def test_choose_breakdown_prefix_requires_completed_and_onboard_orders():
    route = {"id": "route-1", "stops": [
        {"order_id": "a", "stop_type": "PICKUP"},
        {"order_id": "b", "stop_type": "PICKUP"},
        {"order_id": "a", "stop_type": "DELIVERY"},
        {"order_id": "b", "stop_type": "DELIVERY"},
    ]}
    selected, prefix = choose_breakdown_prefix([route])
    assert selected is route
    assert [stop["stop_type"] for stop in prefix] == ["PICKUP", "PICKUP", "DELIVERY"]


def test_choose_breakdown_prefix_rejects_single_order_routes():
    routes = [{"id": "route-1", "stops": [
        {"order_id": "a", "stop_type": "PICKUP"},
        {"order_id": "a", "stop_type": "DELIVERY"},
    ]}]
    with pytest.raises(ValueError, match="completed and onboard"):
        choose_breakdown_prefix(routes)
