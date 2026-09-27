"""Backfill old plan lines without changing the historical plan facts."""

import asyncio
from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import IntegrationError
from app.api.dependencies import get_db
from app.db.models import DeliveryPlan, VehicleRoute
from app.db.models.planning import DeliveryPlanStatus
from app.integrations.routing.osrm import RoadRoute
from app.jobs.backfill_road_geometry import backfill_road_geometry
from app.main import app


BUSINESS_DATE = date(2026, 9, 25)


@pytest.fixture
def seeded_sessions(p1_bootstrap_database_url):
    engine = create_engine(p1_bootstrap_database_url)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def selected_routes(session):
    return session.scalars(
        select(VehicleRoute).join(DeliveryPlan).where(
            DeliveryPlan.business_date == BUSINESS_DATE,
            DeliveryPlan.status.in_((DeliveryPlanStatus.CURRENT, DeliveryPlanStatus.CANDIDATE)),
        ).order_by(DeliveryPlan.version_no, VehicleRoute.route_no)
    ).all()


class RoadProvider:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def build_route(self, waypoints):
        self.calls.append(tuple(point.location_id for point in waypoints))
        if len(self.calls) == self.fail_at:
            raise IntegrationError(code="ROAD_ROUTING_NO_ROUTE", message="NoRoute")
        coordinates = [[waypoints[0].longitude, waypoints[0].latitude]]
        ends = []
        for origin, destination in zip(waypoints, waypoints[1:]):
            coordinates.extend([
                [(origin.longitude + destination.longitude) / 2,
                 (origin.latitude + destination.latitude) / 2 + 0.0002],
                [destination.longitude, destination.latitude],
            ])
            ends.append(len(coordinates) - 1)
        return RoadRoute({"type": "LineString", "coordinates": coordinates}, tuple(ends), 600, 90)


def test_force_backfills_current_and_candidate_without_changing_plan_facts(seeded_sessions):
    with seeded_sessions() as session:
        routes = selected_routes(session)
        assert routes and all(route.route_geometry for route in routes)
        expected = []
        facts = {}
        for route in routes:
            stops = sorted(route.stops, key=lambda stop: stop.sequence_no)
            ids = (route.start_location_id, *(stop.location_id for stop in stops))
            if not stops or route.end_location_id != stops[-1].location_id:
                ids = (*ids, route.end_location_id)
            expected.append(ids)
            facts[route.id] = (
                route.status, route.distance_meters, route.duration_seconds,
                route.delivery_plan.status, route.delivery_plan.total_distance_meters,
                tuple((stop.id, stop.sequence_no, stop.status) for stop in stops),
            )

    provider = RoadProvider()
    skipped = backfill_road_geometry(BUSINESS_DATE, session_factory=seeded_sessions,
                                     routing_provider=provider)
    assert skipped.updated_count == 0
    assert skipped.skipped_count == len(expected)
    assert provider.calls == []

    updated = backfill_road_geometry(BUSINESS_DATE, session_factory=seeded_sessions,
                                     routing_provider=provider, force=True)
    assert updated.updated_count == len(expected)
    assert updated.failed_count == 0
    assert provider.calls == expected
    with seeded_sessions() as session:
        for route in selected_routes(session):
            assert len(route.route_geometry["coordinates"]) > len(route.stops) + 1
            assert route.route_metrics["geometry_provider"] == "OSRM"
            assert len(route.route_metrics["road_leg_end_indices"]) in (len(route.stops), len(route.stops) + 1)
            stops = sorted(route.stops, key=lambda stop: stop.sequence_no)
            assert facts[route.id] == (
                route.status, route.distance_meters, route.duration_seconds,
                route.delivery_plan.status, route.delivery_plan.total_distance_meters,
                tuple((stop.id, stop.sequence_no, stop.status) for stop in stops),
            )

    repeat = backfill_road_geometry(BUSINESS_DATE, session_factory=seeded_sessions,
                                    routing_provider=provider)
    assert repeat.updated_count == 0
    assert repeat.skipped_count == len(expected)
    assert provider.calls == expected


def test_missing_geometry_is_filled_without_force(seeded_sessions):
    with seeded_sessions.begin() as session:
        route = selected_routes(session)[0]
        route_id = route.id
        route.route_geometry = None

    provider = RoadProvider()
    result = backfill_road_geometry(BUSINESS_DATE, session_factory=seeded_sessions,
                                    routing_provider=provider)
    assert result.updated_count == 1
    assert len(provider.calls) == 1
    with seeded_sessions() as session:
        route = session.get(VehicleRoute, route_id)
        assert route.route_metrics["geometry_provider"] == "OSRM"
        assert route.route_geometry["type"] == "LineString"


def test_failed_route_keeps_its_old_line_and_other_routes_continue(seeded_sessions):
    with seeded_sessions() as session:
        routes = selected_routes(session)
        old_lines = {route.id: route.route_geometry for route in routes}
        old_metrics = {route.id: route.route_metrics for route in routes}
        failed_id = routes[1].id

    provider = RoadProvider(fail_at=2)
    result = backfill_road_geometry(BUSINESS_DATE, session_factory=seeded_sessions,
                                    routing_provider=provider, force=True)
    assert result.updated_count == len(routes) - 1
    assert result.failed_count == 1
    with seeded_sessions() as session:
        for route in selected_routes(session):
            if route.id == failed_id:
                assert route.route_geometry == old_lines[route.id]
                assert route.route_metrics == old_metrics[route.id]
            else:
                assert route.route_metrics["geometry_provider"] == "OSRM"


def test_backfilled_current_plan_has_same_workspace_and_simulated_path(seeded_sessions):
    result = backfill_road_geometry(BUSINESS_DATE, session_factory=seeded_sessions,
                                    routing_provider=RoadProvider(), force=True)
    assert result.failed_count == 0

    def override_get_db():
        with seeded_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://testserver") as client:
                params = {"business_date": BUSINESS_DATE.isoformat()}
                workspace = await client.get("/api/operations/workspace", params=params)
                positions = await client.get("/api/operations/simulated-positions", params=params)
                assert workspace.status_code == 200, workspace.text
                assert positions.status_code == 200, positions.text
                routes = {item["id"]: item for item in workspace.json()["data"]["routes"]}
                assert routes and all(item["road_aligned"] for item in routes.values())
                for vehicle in positions.json()["data"]["vehicles"]:
                    assert vehicle["source"] == "SIMULATED"
                    assert vehicle["path"] == routes[vehicle["route_id"]]["path"]

        asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()


def test_unbackfilled_connector_path_includes_terminal_return(seeded_sessions):
    with seeded_sessions.begin() as session:
        route = selected_routes(session)[0]
        route_id = str(route.id)
        route.route_geometry = None
        terminal = [float(route.end_location.longitude), float(route.end_location.latitude)]

    def override_get_db():
        with seeded_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://testserver") as client:
                params = {"business_date": BUSINESS_DATE.isoformat()}
                workspace = await client.get("/api/operations/workspace", params=params)
                positions = await client.get("/api/operations/simulated-positions", params=params)
                assert workspace.status_code == 200, workspace.text
                assert positions.status_code == 200, positions.text
                rendered = next(item for item in workspace.json()["data"]["routes"] if item["id"] == route_id)
                vehicle = next(item for item in positions.json()["data"]["vehicles"] if item["route_id"] == route_id)
                assert rendered["geometry_source"] == "STOP_CONNECTORS"
                assert rendered["road_aligned"] is False
                assert rendered["path"][-1] == terminal
                assert rendered["path"] == vehicle["path"]

        asyncio.run(scenario())
    finally:
        app.dependency_overrides.clear()
