from types import SimpleNamespace
from uuid import UUID

from app.integrations.routing.contracts import RoutingLocation
from app.integrations.routing.osrm import RoadRoute
from app.modules.recovery.deterministic_workflow import RecoveryWorkflow


def _context(*, verified: bool = False):
    start = RoutingLocation(UUID(int=1), 1.30, 103.80)
    stop = RoutingLocation(UUID(int=2), 1.31, 103.81)
    end = RoutingLocation(UUID(int=3), 1.32, 103.82)
    base_route = SimpleNamespace(
        id=UUID(int=4), vehicle_id=UUID(int=5), start_location_id=start.location_id,
        end_location_id=end.location_id,
        stops=(SimpleNamespace(order_id=UUID(int=6), location_id=stop.location_id),),
        route_geometry={"type": "LineString", "coordinates": [
            [103.8, 1.3], [103.81, 1.31], [103.82, 1.32],
        ]} if verified else None,
        route_metrics={"geometry_provider": "OSRM", "road_leg_end_indices": [1, 2]} if verified else None,
    )
    return SimpleNamespace(
        routes=(base_route,), target_orders=(), vehicles=(), locations=(start, stop, end),
        incident_type="MERCHANT_DELAY", affected_route_id=None, incident_location_id=None,
    )


def test_unchanged_route_includes_its_terminal_destination(monkeypatch):
    requested = []

    class Provider:
        def build_route(self, waypoints):
            requested.append(tuple(item.location_id for item in waypoints))
            return RoadRoute({"type": "LineString", "coordinates": [[103.8, 1.3], [103.82, 1.32]]},
                             (1, 1), 300, 30)

    monkeypatch.setattr("app.modules.recovery.deterministic_workflow.get_routing_provider", lambda: Provider())
    context = _context()
    routes = RecoveryWorkflow._build_candidate_road_routes(context, SimpleNamespace(routes=()))
    assert requested == [(UUID(int=1), UUID(int=2), UUID(int=3))]
    assert UUID(int=5) in routes


def test_unchanged_verified_route_reuses_existing_geometry(monkeypatch):
    class Provider:
        def build_route(self, waypoints):
            raise AssertionError("unchanged verified route must be reused")

    monkeypatch.setattr("app.modules.recovery.deterministic_workflow.get_routing_provider", lambda: Provider())
    assert RecoveryWorkflow._build_candidate_road_routes(
        _context(verified=True), SimpleNamespace(routes=())
    ) == {}
