import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from uuid import UUID

import pytest

from app.core.errors import IntegrationError
from app.integrations.routing.contracts import RoutingLocation
from app.integrations.routing.osrm import OsrmRoutingProvider
from app.integrations.routing.distance_matrix import get_routing_provider


def _location(number: int, latitude: float, longitude: float) -> RoutingLocation:
    return RoutingLocation(UUID(int=number), latitude, longitude)


def _response(monkeypatch, payload):
    calls = []

    def fake_open(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("app.integrations.routing.osrm.urlopen", fake_open)
    return calls


def test_osrm_table_builds_road_matrix_in_input_order(monkeypatch):
    locations = (_location(1, 1.3, 103.8), _location(2, 1.31, 103.81))
    calls = _response(monkeypatch, {
        "code": "Ok",
        "durations": [[0, 12.2], [20.1, 0]],
        "distances": [[0, 205.2], [301.8, 0]],
        "sources": [{"distance": 3}, {"distance": 4}],
        "destinations": [{"distance": 3}, {"distance": 4}],
    })

    matrix = OsrmRoutingProvider("http://osrm.local", timeout_seconds=8).build_matrix(locations)

    assert matrix.location_ids == (locations[0].location_id, locations[1].location_id)
    assert matrix.duration_matrix_seconds == ((0, 13), (21, 0))
    assert matrix.distance_matrix_meters == ((0, 205), (302, 0))
    assert "/table/v1/driving/103.8,1.3;103.81,1.31?annotations=duration,distance" in calls[0][0]
    assert calls[0][1] == 8


def test_osrm_route_preserves_leg_boundaries_and_duplicate_stop(monkeypatch):
    waypoints = (
        _location(1, 1.3, 103.8),
        _location(2, 1.31, 103.81),
        _location(3, 1.31, 103.81),
        _location(4, 1.32, 103.82),
    )
    calls = _response(monkeypatch, {
        "code": "Ok",
        "waypoints": [{"distance": 2}, {"distance": 3}, {"distance": 4}],
        "routes": [{
            "distance": 500,
            "duration": 70,
            "legs": [
                {"steps": [{"geometry": {"type": "LineString", "coordinates": [
                    [103.8, 1.3], [103.805, 1.305], [103.81, 1.31],
                ]}}]},
                {"steps": [{"geometry": {"type": "LineString", "coordinates": [
                    [103.81, 1.31], [103.815, 1.312], [103.82, 1.32],
                ]}}]},
            ],
        }],
    })

    road = OsrmRoutingProvider("http://osrm.local").build_route(waypoints)

    assert road.geometry == {"type": "LineString", "coordinates": [
        [103.8, 1.3], [103.805, 1.305], [103.81, 1.31],
        [103.815, 1.312], [103.82, 1.32],
    ]}
    assert road.leg_end_indices == (2, 2, 4)
    assert road.distance_meters == 500
    assert "103.8,1.3;103.81,1.31;103.82,1.32" in calls[0][0]
    assert "steps=true&geometries=geojson&overview=full" in calls[0][0]


@pytest.mark.parametrize("payload", [
    {"code": "NoRoute"},
    {"code": "Ok", "durations": [[0, None], [1, 0]], "distances": [[0, 2], [2, 0]],
     "sources": [{"distance": 0}, {"distance": 0}], "destinations": [{"distance": 0}, {"distance": 0}]},
    {"code": "Ok", "durations": [[0]], "distances": [[0]],
     "sources": [{"distance": 0}, {"distance": 0}], "destinations": [{"distance": 0}, {"distance": 0}]},
    {"code": "Ok", "durations": [[0, 1], [1, 0]], "distances": [[0, 2], [2, 0]],
     "sources": [{"distance": 251}, {"distance": 0}], "destinations": [{"distance": 0}, {"distance": 0}]},
])
def test_osrm_table_rejects_unusable_response(monkeypatch, payload):
    _response(monkeypatch, payload)
    with pytest.raises(IntegrationError):
        OsrmRoutingProvider("http://osrm.local").build_matrix(
            (_location(1, 1.3, 103.8), _location(2, 1.31, 103.81))
        )


def test_osrm_route_rejects_waypoint_far_from_road(monkeypatch):
    _response(monkeypatch, {"code": "Ok", "waypoints": [
        {"distance": 0}, {"distance": 251},
    ], "routes": [{"distance": 2, "duration": 1, "legs": []}]})
    with pytest.raises(IntegrationError):
        OsrmRoutingProvider("http://osrm.local").build_route(
            (_location(1, 1.3, 103.8), _location(2, 1.31, 103.81))
        )


def test_http_error_with_no_route_code_is_reported_as_unroutable(monkeypatch):
    def fail_open(url, timeout):
        raise HTTPError(url, 400, "NoRoute", {}, io.BytesIO(b'{"code":"NoRoute"}'))

    monkeypatch.setattr("app.integrations.routing.osrm.urlopen", fail_open)
    with pytest.raises(IntegrationError) as error:
        OsrmRoutingProvider("http://osrm.local").build_route(
            (_location(1, 1.3, 103.8), _location(2, 1.31, 103.81))
        )
    assert error.value.code == "ROAD_ROUTING_NO_ROUTE"


def test_osrm_mode_requires_an_explicit_base_url(monkeypatch):
    monkeypatch.setattr("app.integrations.routing.distance_matrix.get_settings", lambda: SimpleNamespace(
        routing_provider="osrm", osrm_base_url=None, osrm_timeout_seconds=8,
    ))
    with pytest.raises(IntegrationError) as error:
        get_routing_provider()
    assert error.value.code == "ROAD_ROUTING_NOT_CONFIGURED"


def test_osrm_route_rejects_disconnected_steps(monkeypatch):
    _response(monkeypatch, {
        "code": "Ok", "waypoints": [{"distance": 0}, {"distance": 0}],
        "routes": [{"distance": 200, "duration": 20, "legs": [{"steps": [
            {"geometry": {"type": "LineString", "coordinates": [[103.8, 1.3], [103.801, 1.301]]}},
            {"geometry": {"type": "LineString", "coordinates": [[103.9, 1.4], [103.81, 1.31]]}},
        ]}]}],
    })
    with pytest.raises(IntegrationError, match="disconnected"):
        OsrmRoutingProvider("http://osrm.local").build_route(
            (_location(1, 1.3, 103.8), _location(2, 1.31, 103.81))
        )


def test_osrm_route_keeps_small_snap_gap_between_legs(monkeypatch):
    _response(monkeypatch, {
        "code": "Ok", "waypoints": [{"distance": 0}, {"distance": 0}, {"distance": 0}],
        "routes": [{"distance": 300, "duration": 30, "legs": [
            {"steps": [{"geometry": {"type": "LineString", "coordinates": [
                [103.7, 1.3], [103.76508, 1.315086],
            ]}}]},
            {"steps": [{"geometry": {"type": "LineString", "coordinates": [
                [103.765078, 1.315091], [103.8, 1.32],
            ]}}]},
        ]}],
    })

    road = OsrmRoutingProvider("http://osrm.local").build_route((
        _location(1, 1.3, 103.7),
        _location(2, 1.315, 103.765),
        _location(3, 1.32, 103.8),
    ))

    assert road.geometry["coordinates"] == [
        [103.7, 1.3], [103.76508, 1.315086],
        [103.765078, 1.315091], [103.8, 1.32],
    ]
    assert road.leg_end_indices == (1, 3)


def test_osrm_route_with_only_one_distinct_waypoint_snaps_to_road(monkeypatch):
    calls = _response(monkeypatch, {"code": "Ok", "waypoints": [{
        "distance": 10, "location": [103.8001, 1.3001],
    }]})
    road = OsrmRoutingProvider("http://osrm.local").build_route((
        _location(1, 1.3, 103.8), _location(2, 1.3, 103.8),
    ))
    assert "/nearest/v1/driving/103.8,1.3?number=1" in calls[0][0]
    assert road.geometry["coordinates"] == [[103.8001, 1.3001], [103.8001, 1.3001]]
    assert road.leg_end_indices == (1,)
