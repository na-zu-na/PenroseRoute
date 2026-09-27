import asyncio
from datetime import datetime
from uuid import uuid4, UUID

import pytest
from sqlalchemy import select
from app.db.models import Vehicle, Driver, VehicleDriverAssignment, RecoveryPlan, DeliveryPlan, Incident
from app.db.models.fleet import ResourceStatus, AssignmentStatus
from tests.integration.test_recovery_workflow import (
    recovery_client, prepare_route, create_incident, _p0_decision_override,
    fixed_recovery_clock, isolated_recovery_seed,
)


def add_resources(session):
    for n in range(3):
        v, d = uuid4(), uuid4()
        session.add(Vehicle(id=v, vehicle_code=f'OPTIONS-{n}', name=f'Options {n}',
            capacity_load_units=8, status=ResourceStatus.AVAILABLE,
            current_location_id=UUID('10000000-0000-0000-0000-000000000001'),
            current_location_recorded_at=datetime.fromisoformat('2026-09-25T10:05:00+08:00')))
        session.add(Driver(id=d, driver_code=f'OPTIONS-{n}', name=f'Driver {n}', status=ResourceStatus.AVAILABLE))
        session.flush()
        session.add(VehicleDriverAssignment(id=uuid4(), vehicle_id=v, driver_id=d,
            assigned_from_at=datetime.fromisoformat('2026-09-25T08:00:00+08:00'),
            assigned_until_at=datetime.fromisoformat('2026-09-25T18:00:00+08:00'),
            status=AssignmentStatus.PLANNED))
    session.commit()


def test_options_rank_and_choose_nonrecommended_candidate_atomically():
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        _p0_decision_override(session)
        async def run():
            incident_id = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident_id}/recovery-options', json={})
            assert response.status_code == 201, response.text
            body = response.json()['data']
            options = body['candidates']
            assert len(options) == 3
            assert [o['priority'] for o in options] == [1, 2, 3]
            assert body['recommended_recovery_plan_id'] == options[0]['recovery_plan_id']
            assert len({o['candidate_delivery_plan_id'] for o in options}) == 3
            listed = await client.get(f'/api/incidents/{incident_id}/recovery-options')
            assert listed.status_code == 200, listed.text
            assert [o['recovery_plan_id'] for o in listed.json()['data']['candidates']] == [o['recovery_plan_id'] for o in options]
            for option in options:
                comparison = await client.get(f"/api/recovery-plans/{option['recovery_plan_id']}/comparison")
                assert comparison.status_code == 200, comparison.text
                assert comparison.json()['data']['reviewable'] is True
            selected = options[1]
            approved = await client.post(f"/api/recovery-plans/{selected['recovery_plan_id']}/approve", json={'decision_reason':'Dispatcher prefers this vehicle allocation'})
            assert approved.status_code == 200, approved.text
            duplicate = await client.post(f"/api/recovery-plans/{options[0]['recovery_plan_id']}/approve", json={'decision_reason':'Must not activate twice'})
            assert duplicate.status_code == 409
            refreshed = await client.get(f'/api/incidents/{incident_id}/recovery-options')
            assert not any(o['reviewable'] for o in refreshed.json()['data']['candidates'])
            return incident_id, options, selected
        incident_id, options, selected = asyncio.run(run())
        session.expire_all()
        assert session.get(Incident, incident_id).status.value == 'RESOLVED'
        for option in options:
            plan = session.get(DeliveryPlan, option['candidate_delivery_plan_id'])
            recovery = session.get(RecoveryPlan, option['recovery_plan_id'])
            assert plan.status.value == ('CURRENT' if option == selected else 'CANCELLED')
            assert recovery.status.value == 'DECIDED'


def test_reject_one_option_leaves_others_reviewable():
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        _p0_decision_override(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert response.status_code == 201, response.text
            options = response.json()['data']['candidates']
            rejected = await client.post(f"/api/recovery-plans/{options[0]['recovery_plan_id']}/reject", json={'decision_reason':'Prefer another option'})
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()['data']['incident_status'] == 'REVIEW'
            approved = await client.post(f"/api/recovery-plans/{options[1]['recovery_plan_id']}/approve", json={'decision_reason':'Choose remaining alternative'})
            assert approved.status_code == 200, approved.text
        asyncio.run(run())


def test_stale_options_cannot_be_approved_and_can_be_regenerated():
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        _p0_decision_override(session)
        async def setup():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert response.status_code == 201, response.text
            return incident, response.json()['data']
        incident, before = asyncio.run(setup())
        spare = session.scalar(select(Vehicle).where(Vehicle.vehicle_code == 'OPTIONS-0'))
        spare.capacity_load_units = 9
        session.commit()
        async def run():
            response = await client.post(f"/api/recovery-plans/{before['candidates'][0]['recovery_plan_id']}/approve", json={'decision_reason':'Stale'})
            assert response.status_code == 409, response.text
            assert response.json()['code'] == 'RECOVERY_CONTEXT_CHANGED'
            refreshed = await client.post(f'/api/incidents/{incident}/recovery-options', json={'regenerate': True})
            assert refreshed.status_code == 201, refreshed.text
            assert refreshed.json()['data']['batch_id'] != before['batch_id']
            assert refreshed.json()['data']['candidates']
        asyncio.run(run())


def test_duplicate_generation_conflicts_and_limit_is_validated():
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        async def run():
            incident = await create_incident(client)
            invalid = await client.post(f'/api/incidents/{incident}/recovery-options', json={'max_candidates': 10})
            assert invalid.status_code == 422
            first = await client.post(f'/api/incidents/{incident}/recovery-options', json={'max_candidates': 2})
            assert first.status_code == 201, first.text
            assert len(first.json()['data']['candidates']) == 2
            duplicate = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert duplicate.status_code == 409
        asyncio.run(run())


def test_two_dispatchers_select_different_options_only_one_commits(p1_bootstrap_database_url):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.errors import Conflict
    from app.modules.incidents.workflow import VehicleIncidentWorkflow
    from app.modules.recovery.options import RecoveryOptionsWorkflow
    from app.modules.decisions.service import DeterministicDecisionService
    from tests.integration.test_recovery_workflow import BUSINESS_DATE, VEHICLE_ID
    from datetime import date
    engine = create_engine(p1_bootstrap_database_url)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    try:
        with sessions() as session:
            prepare_route(session)
            add_resources(session)
            incident = VehicleIncidentWorkflow(session).report_unavailable(
                business_date=date.fromisoformat(BUSINESS_DATE), vehicle_id=UUID(VEHICLE_ID),
                detected_at=datetime.fromisoformat('2026-09-25T10:05:00+08:00'),
                location_code='CONCURRENT-OPTIONS', address_text='Breakdown location',
                latitude=1.305, longitude=103.755)
            options = RecoveryOptionsWorkflow(session).start(incident.incident_id)['candidates']
        barrier = Barrier(2)
        def approve(option):
            barrier.wait(timeout=10)
            try:
                DeterministicDecisionService(sessions,
                    clock=lambda: datetime.fromisoformat('2026-09-25T10:06:00+08:00')).decide(
                        option['recovery_plan_id'], 'APPROVE', 'Concurrent selection', 'dispatcher-test')
                return 'APPROVED'
            except Conflict:
                return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(approve, o) for o in options[:2]]
            assert sorted(f.result(timeout=15) for f in futures) == ['APPROVED', 'CONFLICT']
        with sessions() as session:
            plans = [session.get(DeliveryPlan, o['candidate_delivery_plan_id']) for o in options]
            assert sum(p.status.value == 'CURRENT' for p in plans) == 1
            assert sum(p.status.value == 'CANCELLED' for p in plans) == len(plans) - 1
    finally:
        engine.dispose()


def test_modify_any_option_replaces_batch():
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        _p0_decision_override(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert response.status_code == 201, response.text
            original = response.json()['data']
            choices = [o for o in original['candidates'] if o['replanning_scope'] != 'ALL_REMAINING']
            assert choices, 'Fixture must provide a narrower-scope alternative'
            result = await client.post(f"/api/recovery-plans/{choices[0]['recovery_plan_id']}/modify", json={'decision_reason':'Widen recovery scope'})
            assert result.status_code == 200, result.text
            data = result.json()['data']
            assert data['batch_id'] != original['batch_id']
            assert data['outcome'] in ('PENDING_REVIEW', 'NO_FEASIBLE_RECOVERY')
            if data['outcome'] == 'NO_FEASIBLE_RECOVERY':
                assert not data['candidates']
                assert data['manual_intervention_required']
            for old in original['candidates']:
                comparison = await client.get(f"/api/recovery-plans/{old['recovery_plan_id']}/comparison")
                assert comparison.json()['data']['reviewable'] is False
        asyncio.run(run())


def test_solver_failure_does_not_publish_partial_candidates(monkeypatch):
    from app.modules.recovery import options as module
    from app.integrations.optimization.contracts import SolverResult, SolverStatus
    original = module.ORToolsSolver.solve
    calls = 0
    def solve(solver, source):
        nonlocal calls
        calls += 1
        if calls == 2:
            return SolverResult(SolverStatus.ERROR, (), (), 0, 0, 'Injected failure after first solution')
        return original(solver, source)
    monkeypatch.setattr(module.ORToolsSolver, 'solve', solve)
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        async def run():
            incident = await create_incident(client)
            result = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert result.status_code == 500, result.text
            read = await client.get(f'/api/incidents/{incident}/recovery-options')
            assert read.json()['data']['outcome'] == 'FAILED'
            assert read.json()['data']['candidates'] == []
            return incident
        incident = asyncio.run(run())
        session.expire_all()
        attempts = list(session.scalars(select(RecoveryPlan).where(RecoveryPlan.incident_id == incident)))
        assert all(a.candidate_delivery_plan_id is None for a in attempts)


def test_merchant_delay_generates_alternatives(monkeypatch):
    from datetime import date, timedelta, timezone
    from tests.integration.test_p0_non_agent_e2e import demo_client, seed_demo, _post_ok
    from app.modules.recovery import deterministic_context
    day = datetime(2026, 11, 8, tzinfo=timezone.utc)
    now = day + timedelta(hours=8, minutes=5)
    monkeypatch.setattr(deterministic_context, '_now', lambda: now)
    with demo_client(lambda: now + timedelta(minutes=1)) as (client, sessions):
        ids = seed_demo(sessions, day.date())
        _post_ok(client, '/api/planning/generate', {'business_date':day.date().isoformat()}, 201)
        with sessions() as session, session.begin():
            session.get(Vehicle, ids['spare']).status = ResourceStatus.AVAILABLE
        incident = _post_ok(client, '/api/incidents/merchant-delay', {
            'business_date':day.date().isoformat(), 'merchant_id':str(ids['merchants'][0]),
            'updated_ready_at':(day + timedelta(hours=8, minutes=15)).isoformat(),
            'detected_at':now.isoformat()}, 201)['incident_id']
        data = _post_ok(client, f'/api/incidents/{incident}/recovery-options', {}, 201)
        assert len(data['candidates']) >= 2
        assert all(o['validation_status'] == 'VALID' for o in data['candidates'])


def test_expired_schedule_cannot_be_approved_without_fact_changes():
    from app.modules.decisions.service import DeterministicDecisionService
    from sqlalchemy.orm import Session
    from app.core.errors import Conflict
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert response.status_code == 201, response.text
            return response.json()['data']['candidates'][0]
        selected = asyncio.run(run())
        service = DeterministicDecisionService(lambda: Session(bind=session.get_bind(),
            autoflush=False, expire_on_commit=False, join_transaction_mode='create_savepoint'),
            clock=lambda: datetime.fromisoformat('2026-09-26T10:06:00+08:00'))
        with pytest.raises(Conflict) as caught:
            service.decide(selected['recovery_plan_id'], 'APPROVE', 'Too late', 'dispatcher-test')
        assert caught.value.code == 'CANDIDATE_SCHEDULE_STALE'
        session.expire_all()
        assert session.get(DeliveryPlan, selected['candidate_delivery_plan_id']).status.value == 'CANDIDATE'


def test_approval_before_incident_detection_is_a_conflict_not_server_error():
    from sqlalchemy.orm import Session
    from app.core.errors import Conflict
    from app.modules.decisions.service import DeterministicDecisionService

    with recovery_client() as (client, session):
        prepare_route(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery', json={})
            assert response.status_code == 201, response.text
            return response.json()['data']['reviewable_recovery_plan_id']
        recovery_id = asyncio.run(run())
        service = DeterministicDecisionService(lambda: Session(bind=session.get_bind(),
            autoflush=False, expire_on_commit=False, join_transaction_mode='create_savepoint'),
            clock=lambda: datetime.fromisoformat('2026-09-25T10:04:00+08:00'))
        with pytest.raises(Conflict) as caught:
            service.decide(recovery_id, 'APPROVE', 'Review', 'dispatcher-test')
        assert caught.value.code == 'INCIDENT_NOT_OCCURRED_YET'
        session.expire_all()
        assert session.get(RecoveryPlan, recovery_id).status.value == 'PENDING_REVIEW'


def test_development_demo_clock_allows_approval_at_simulated_operational_time(monkeypatch):
    from sqlalchemy.orm import Session
    from app.api.routes.decisions import get_decision_service
    from app.core.config import get_settings
    from app.db import session as db_session

    with recovery_client() as (client, session):
        prepare_route(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery', json={})
            assert response.status_code == 201, response.text
            return response.json()['data']['reviewable_recovery_plan_id']
        recovery_id = asyncio.run(run())
        monkeypatch.setenv('APP_ENV', 'development')
        monkeypatch.setenv('DEMO_DECISION_NOW', '2026-09-25T10:06:00+08:00')
        get_settings.cache_clear()
        monkeypatch.setattr(db_session, 'SessionLocal', lambda: Session(bind=session.get_bind(),
            autoflush=False, expire_on_commit=False, join_transaction_mode='create_savepoint'))
        try:
            service = get_decision_service('deterministic', None)
            result = service.decide(recovery_id, 'APPROVE', 'Simulated review', 'dispatcher-test')
            assert result['candidate_status'] == 'CURRENT'
            assert result['incident_status'] == 'RESOLVED'
            assert result['reviewed_at'] == '2026-09-25T10:06:00+08:00'
        finally:
            get_settings.cache_clear()


def test_demo_clock_does_not_change_decisions_for_another_business_date(monkeypatch):
    from sqlalchemy.orm import Session
    from app.api.routes.decisions import get_decision_service
    from app.core.config import get_settings
    from app.db import session as db_session

    with recovery_client() as (client, session):
        prepare_route(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery', json={})
            assert response.status_code == 201, response.text
            return response.json()['data']['reviewable_recovery_plan_id']
        recovery_id = asyncio.run(run())
        monkeypatch.setenv('APP_ENV', 'development')
        monkeypatch.setenv('DEMO_DECISION_NOW', '2026-09-28T17:56:00+08:00')
        get_settings.cache_clear()
        monkeypatch.setattr(db_session, 'SessionLocal', lambda: Session(bind=session.get_bind(),
            autoflush=False, expire_on_commit=False, join_transaction_mode='create_savepoint'))
        try:
            service = get_decision_service('deterministic', None)
            result = service.decide(recovery_id, 'APPROVE', 'Ordinary review', 'dispatcher-test')
            assert result['reviewed_at'] != '2026-09-28T17:56:00+08:00'
        finally:
            get_settings.cache_clear()


def test_legacy_pending_candidate_must_be_decided_before_switching_workflows():
    with recovery_client() as (client, session):
        prepare_route(session)
        async def run():
            incident = await create_incident(client)
            original = await client.post(f'/api/incidents/{incident}/recovery', json={})
            assert original.status_code == 201, original.text
            result = await client.post(f'/api/incidents/{incident}/recovery-options', json={'regenerate': True})
            assert result.status_code == 409, result.text
            assert result.json()['code'] == 'LEGACY_RECOVERY_PENDING'
        asyncio.run(run())


def test_stale_facts_do_not_prevent_rejecting_an_option():
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        _p0_decision_override(session)
        async def setup():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert response.status_code == 201, response.text
            return response.json()['data']['candidates'][0]['recovery_plan_id']
        selected = asyncio.run(setup())
        session.scalar(select(Vehicle).where(Vehicle.vehicle_code == 'OPTIONS-0')).capacity_load_units = 9
        session.commit()
        async def run():
            result = await client.post(f'/api/recovery-plans/{selected}/reject', json={'decision_reason':'Facts changed, discard this option'})
            assert result.status_code == 200, result.text
            assert result.json()['data']['dispatcher_decision'] == 'REJECT'
        asyncio.run(run())


def test_approval_requires_time_to_reach_first_replanned_stop():
    from datetime import timedelta
    from sqlalchemy.orm import Session
    from app.core.errors import Conflict
    from app.db.repositories.plan_repository import PlanRepository
    from app.modules.decisions.service import DeterministicDecisionService
    with recovery_client() as (client, session):
        prepare_route(session)
        add_resources(session)
        async def run():
            incident = await create_incident(client)
            response = await client.post(f'/api/incidents/{incident}/recovery-options', json={})
            assert response.status_code == 201, response.text
            return response.json()['data']['candidates'][0]
        selected = asyncio.run(run())
        candidate = PlanRepository(session).get_plan_with_routes(UUID(selected['candidate_delivery_plan_id']))
        first_arrival = min(s.planned_arrival_at for r in candidate.routes for s in r.stops
            if s.status != 'COMPLETED' and s.sequence_no > (r.route_metrics or {}).get('preserved_stop_count', 0))
        now = first_arrival - timedelta(seconds=1)
        service = DeterministicDecisionService(lambda: Session(bind=session.get_bind(),
            autoflush=False, expire_on_commit=False, join_transaction_mode='create_savepoint'), clock=lambda: now)
        with pytest.raises(Conflict) as caught:
            service.decide(selected['recovery_plan_id'], 'APPROVE', 'Late approval', 'dispatcher-test')
        assert caught.value.code == 'CANDIDATE_SCHEDULE_STALE'
        session.expire_all()
        assert session.get(DeliveryPlan, selected['candidate_delivery_plan_id']).status.value == 'CANDIDATE'
        assert datetime.fromisoformat(selected['review_deadline_at']) < now
