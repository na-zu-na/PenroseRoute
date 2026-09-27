import asyncio
from sqlalchemy import select

from app.db.models import DeliveryPlan, Order
from app.db.models.planning import DeliveryPlanStatus
from app.integrations.routing.osrm import RoadRoute
from app.integrations.routing.provider import DeterministicRoutingProvider
from tests.integration.test_planning_workflow import planning_client, seed_planning_facts


def test_workspace_returns_readiness_without_a_plan() -> None:
    with planning_client() as (client, session):
        day = seed_planning_facts(session)

        async def scenario():
            response = await client.get('/api/operations/workspace', params={'business_date': str(day)})
            assert response.status_code == 200, response.text
            data = response.json()['data']
            assert data['plan'] is None
            assert data['routes'] == []
            assert data['readiness']['orders'] == 2
            assert data['readiness']['vehicles'] == 1
            assert data['readiness']['drivers'] == 1

        asyncio.run(scenario())


def test_osrm_draft_workspace_uses_ordered_road_geometry(monkeypatch) -> None:
    from app.modules.planning import workflow as planning_workflow

    with planning_client() as (client, session):
        day = seed_planning_facts(session)
        calls = []

        class RoadProvider:
            def build_matrix(self, locations):
                assert not session.in_transaction()
                return DeterministicRoutingProvider().build_matrix(locations)

            def build_route(self, waypoints):
                assert not session.in_transaction()
                calls.append(tuple(point.location_id for point in waypoints))
                coordinates = [[waypoints[0].longitude, waypoints[0].latitude]]
                ends = []
                for origin, destination in zip(waypoints, waypoints[1:]):
                    coordinates.extend([
                        [(origin.longitude + destination.longitude) / 2,
                         (origin.latitude + destination.latitude) / 2 + 0.0002],
                        [destination.longitude, destination.latitude],
                    ])
                    ends.append(len(coordinates) - 1)
                return RoadRoute({"type": "LineString", "coordinates": coordinates},
                                 tuple(ends), 600, 90)

        monkeypatch.setattr(planning_workflow, "get_routing_provider", lambda: RoadProvider())

        async def scenario():
            generated = await client.post('/api/planning/drafts', json={'business_date': str(day)})
            assert generated.status_code == 201, generated.text
            workspace = await client.get('/api/operations/workspace', params={'business_date': str(day)})
            assert workspace.status_code == 200, workspace.text
            route = workspace.json()['data']['routes'][0]
            assert route['geometry_source'] == 'STORED_GEOMETRY'
            assert route['road_aligned'] is True
            assert len(route['path']) == 5
            assert route['path'][1][1] != (route['path'][0][1] + route['path'][2][1]) / 2
            assert len(calls) == 1
            assert len(calls[0]) == 3
            stored = session.get(DeliveryPlan, generated.json()['data']['delivery_plan_id'])
            assert stored.routes[0].route_geometry['coordinates'] == route['path']
            assert stored.routes[0].route_metrics['geometry_provider'] == 'OSRM'
            assert stored.routes[0].route_metrics['road_leg_end_indices'] == [2, 4]

        asyncio.run(scenario())


def test_stored_legacy_line_is_not_labeled_road_aligned() -> None:
    with planning_client() as (client, session):
        day = seed_planning_facts(session)

        async def scenario():
            generated = await client.post('/api/planning/drafts', json={'business_date': str(day)})
            assert generated.status_code == 201, generated.text
            plan = session.get(DeliveryPlan, generated.json()['data']['delivery_plan_id'])
            route = plan.routes[0]
            route.route_geometry = {'type': 'LineString', 'coordinates': [[103.8, 1.3], [103.82, 1.32]]}
            session.commit()
            workspace = await client.get('/api/operations/workspace', params={'business_date': str(day)})
            assert workspace.status_code == 200, workspace.text
            rendered = workspace.json()['data']['routes'][0]
            assert rendered['geometry_source'] == 'STORED_GEOMETRY'
            assert rendered['road_aligned'] is False

        asyncio.run(scenario())


def test_draft_review_confirm_and_current_workspace() -> None:
    with planning_client() as (client, session):
        day = seed_planning_facts(session)

        async def scenario():
            first = await client.post('/api/planning/drafts', json={'business_date': str(day)})
            assert first.status_code == 201, first.text
            assert first.json()['data']['status'] == 'DRAFT'
            first_id = first.json()['data']['delivery_plan_id']
            assert session.scalar(select(DeliveryPlan).where(DeliveryPlan.id == first_id)).activated_at is None

            second = await client.post('/api/planning/drafts', json={'business_date': str(day)})
            assert second.status_code == 201, second.text
            second_id = second.json()['data']['delivery_plan_id']
            assert second.json()['data']['version_no'] == first.json()['data']['version_no'] + 1
            session.expire_all()
            assert session.scalar(select(DeliveryPlan).where(DeliveryPlan.id == first_id)).status is DeliveryPlanStatus.CANCELLED

            stale = await client.post(f'/api/planning/drafts/{first_id}/confirm')
            assert stale.status_code == 409, stale.text
            review = await client.get('/api/operations/workspace', params={'business_date': str(day)})
            assert review.status_code == 200, review.text
            draft = review.json()['data']
            assert draft['plan']['status'] == 'DRAFT'
            assert draft['plan']['id'] == second_id
            assert len(draft['routes']) == 1
            assert len(draft['routes'][0]['stops']) == 2
            assert draft['routes'][0]['vehicle'].startswith('T9-VEH-')
            assert draft['routes'][0]['geometry_source'] == 'STOP_CONNECTORS'
            assert draft['routes'][0]['road_aligned'] is False
            assert len(draft['unassigned']) == 1

            confirmed = await client.post(f'/api/planning/drafts/{second_id}/confirm')
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()['data']['status'] == 'CURRENT'
            assert confirmed.json()['data']['activated_at'] is not None
            repeated = await client.post(f'/api/planning/drafts/{second_id}/confirm')
            assert repeated.status_code == 409
            current = await client.get('/api/operations/workspace', params={'business_date': str(day)})
            assert current.status_code == 200, current.text
            assert current.json()['data']['plan']['status'] == 'CURRENT'
            assert current.json()['data']['on_time_rate'] is None
            conflict = await client.post('/api/planning/generate', json={'business_date': str(day)})
            assert conflict.status_code == 409

        asyncio.run(scenario())


def test_confirm_rejects_changed_planning_input() -> None:
    with planning_client() as (client, session):
        day = seed_planning_facts(session)

        async def scenario():
            response = await client.post('/api/planning/drafts', json={'business_date': str(day)})
            assert response.status_code == 201, response.text
            plan_id = response.json()['data']['delivery_plan_id']
            order = session.scalar(select(Order).where(Order.business_date == day).order_by(Order.order_code))
            assert order is not None
            order.pickup_service_seconds += 60
            session.commit()
            confirmed = await client.post(f'/api/planning/drafts/{plan_id}/confirm')
            assert confirmed.status_code == 409, confirmed.text
            assert confirmed.json()['code'] == 'PLANNING_INPUT_CHANGED'
            session.expire_all()
            plan = session.scalar(select(DeliveryPlan).where(DeliveryPlan.id == plan_id))
            assert plan.status is DeliveryPlanStatus.DRAFT

        asyncio.run(scenario())
