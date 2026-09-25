import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.api.routes.recovery import require_operations_user
from app.db.models import (
    DeliveryPlan,
    DeliveryPlanOrder,
    Incident,
    Order,
    RecoveryPlan,
    RouteStop,
    Vehicle,
    VehicleRoute,
)
from app.db.models.fleet import ResourceStatus
from app.db.models.planning import DeliveryPlanStatus, StopStatus, StopType
from app.db.models.recovery import IncidentType, RecoveryPlanStatus
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.session import engine
from app.integrations.optimization.contracts import SolverResult, SolverStatus
from app.integrations.optimization.result_validator import ValidationIssue
from app.main import app
from app.modules.recovery.deterministic_context import materialize_recovery_context
from app.modules.recovery.deterministic_orchestration import build_recovery_solver_input
from app.db.models.recovery import ReplanningScope
from app.integrations.routing.distance_matrix import build_distance_time_matrix


BUSINESS_DATE = "2026-09-25"
VEHICLE_ID = "50000000-0000-0000-0000-000000000002"
ORDER_IDS = (
    "40000000-0000-0000-0000-000000000003",
    "40000000-0000-0000-0000-000000000004",
    "40000000-0000-0000-0000-000000000005",
)


@pytest.fixture(autouse=True)
def fixed_recovery_clock(monkeypatch) -> None:
    from app.modules.recovery import deterministic_context as recovery_context

    monkeypatch.setattr(
        recovery_context,
        "_now",
        lambda: datetime.fromisoformat("2026-09-25T10:05:00+08:00"),
        raising=False,
    )


@contextmanager
def recovery_client() -> Iterator[tuple[httpx.AsyncClient, Session]]:
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(
        bind=connection,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    def override_get_db() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_operations_user] = lambda: "dispatcher"
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    )
    try:
        yield client, session
    finally:
        asyncio.run(client.aclose())
        app.dependency_overrides.clear()
        session.close()
        outer_transaction.rollback()
        connection.close()


def prepare_route(session: Session) -> None:
    vehicle = session.get(Vehicle, VEHICLE_ID)
    assert vehicle is not None
    vehicle.status = ResourceStatus.ACTIVE
    states = (
        (OrderExecutionStatus.COMPLETED, OrderRiskStatus.NORMAL),
        (OrderExecutionStatus.PICKED_UP, OrderRiskStatus.AT_RISK),
        (OrderExecutionStatus.PLANNED, OrderRiskStatus.NORMAL),
    )
    for order_id, (execution, risk) in zip(ORDER_IDS, states, strict=True):
        order = session.get(Order, order_id)
        assert order is not None
        order.execution_status = execution
        order.risk_status = risk
    session.commit()


async def create_incident(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/incidents/vehicle-unavailable",
        json={
            "business_date": BUSINESS_DATE,
            "vehicle_id": VEHICLE_ID,
            "location": {
                "location_code": "T14-BREAKDOWN-001",
                "address_text": "Task 14 breakdown point",
                "latitude": 1.305,
                "longitude": 103.755,
            },
            "detected_at": "2026-09-25T10:05:00+08:00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["incident_id"]


def test_recovery_expands_only_after_infeasible_and_builds_handover_candidate(
    monkeypatch,
) -> None:
    from app.modules.recovery import deterministic_orchestration as recovery_orchestration

    with recovery_client() as (client, session):
        prepare_route(session)
        original_matrix = recovery_orchestration.build_distance_time_matrix
        original_solve = recovery_orchestration.ORToolsSolver.solve

        def checked_matrix(*args, **kwargs):
            assert not session.in_transaction()
            return original_matrix(*args, **kwargs)

        def checked_solve(solver, solver_input):
            assert not session.in_transaction()
            return original_solve(solver, solver_input)

        monkeypatch.setattr(
            recovery_orchestration, "build_distance_time_matrix", checked_matrix
        )
        monkeypatch.setattr(
            recovery_orchestration.ORToolsSolver, "solve", checked_solve
        )

        async def scenario() -> tuple[str, dict]:
            incident_id = await create_incident(client)
            response = await client.post(
                f"/api/incidents/{incident_id}/deterministic-recovery", json={}
            )
            assert response.status_code == 201, response.text
            return incident_id, response.json()

        incident_id, body = asyncio.run(scenario())
        assert body["code"] == "RECOVERY_PENDING_REVIEW"
        assert body["data"]["outcome"] == "PENDING_REVIEW"
        attempts = body["data"]["attempts_created"]
        assert [item["replanning_scope"] for item in attempts] == [
            "AFFECTED_ROUTE",
            "CROSS_ROUTE",
        ]
        assert attempts[0]["solver_status"] == "INFEASIBLE"
        assert attempts[1]["status"] == "PENDING_REVIEW"

        session.expire_all()
        candidate_id = body["data"]["candidate_delivery_plan_id"]
        candidate = session.get(DeliveryPlan, candidate_id)
        assert candidate is not None
        assert candidate.status is DeliveryPlanStatus.CANDIDATE
        assert session.scalar(
            select(func.count())
            .select_from(DeliveryPlanOrder)
            .where(DeliveryPlanOrder.delivery_plan_id == candidate.id)
        ) == 6
        handover = session.scalar(
            select(RouteStop)
            .join(RouteStop.vehicle_route)
            .where(
                RouteStop.stop_type == StopType.HANDOVER,
                RouteStop.source_incident_id == incident_id,
                RouteStop.order_id == ORDER_IDS[1],
            )
        )
        assert handover is not None
        assert handover.status is StopStatus.PLANNED
        incident = session.get(Incident, incident_id)
        assert incident is not None and incident.status.value == "REVIEW"
        assert session.scalar(
            select(func.count()).select_from(RecoveryPlan).where(
                RecoveryPlan.incident_id == incident_id
            )
        ) == 2


def test_recovery_solver_error_persists_one_attempt_and_stops(monkeypatch) -> None:
    from app.modules.recovery import deterministic_orchestration as recovery_orchestration

    with recovery_client() as (client, session):
        prepare_route(session)

        def solver_error(*args, **kwargs) -> SolverResult:
            return SolverResult(
                status=SolverStatus.ERROR,
                routes=(),
                unassigned_orders=(),
                total_distance_meters=0,
                total_duration_seconds=0,
                diagnostic="forced failure",
            )

        monkeypatch.setattr(
            recovery_orchestration.ORToolsSolver, "solve", solver_error
        )

        async def scenario() -> tuple[str, httpx.Response]:
            incident_id = await create_incident(client)
            response = await client.post(
                f"/api/incidents/{incident_id}/deterministic-recovery", json={}
            )
            return incident_id, response

        incident_id, response = asyncio.run(scenario())
        assert response.status_code == 500
        assert response.json()["code"] == "RECOVERY_SOLVER_ERROR"
        session.expire_all()
        attempts = list(
            session.scalars(
                select(RecoveryPlan).where(RecoveryPlan.incident_id == incident_id)
            )
        )
        assert len(attempts) == 1
        assert attempts[0].status is RecoveryPlanStatus.DRAFT
        assert attempts[0].solver_status.value == "ERROR"
        assert attempts[0].candidate_delivery_plan_id is None


def test_all_infeasible_scopes_are_persisted_without_candidate(monkeypatch) -> None:
    from app.modules.recovery import deterministic_orchestration as recovery_orchestration

    with recovery_client() as (client, session):
        prepare_route(session)

        def infeasible(*args, **kwargs) -> SolverResult:
            return SolverResult(
                status=SolverStatus.INFEASIBLE,
                routes=(),
                unassigned_orders=(),
                total_distance_meters=0,
                total_duration_seconds=0,
                diagnostic="forced infeasible",
            )

        monkeypatch.setattr(
            recovery_orchestration.ORToolsSolver, "solve", infeasible
        )

        async def scenario() -> tuple[str, httpx.Response]:
            incident_id = await create_incident(client)
            response = await client.post(
                f"/api/incidents/{incident_id}/deterministic-recovery", json={}
            )
            return incident_id, response

        incident_id, response = asyncio.run(scenario())
        assert response.status_code == 201, response.text
        data = response.json()["data"]
        assert data["outcome"] == "NO_FEASIBLE_RECOVERY"
        assert data["manual_intervention_required"] is True
        assert data["candidate_delivery_plan_id"] is None
        session.expire_all()
        attempts = list(
            session.scalars(
                select(RecoveryPlan)
                .where(RecoveryPlan.incident_id == incident_id)
                .order_by(RecoveryPlan.attempt_no)
            )
        )
        assert [item.replanning_scope.value for item in attempts] == [
            "AFFECTED_ROUTE",
            "CROSS_ROUTE",
            "ALL_REMAINING",
        ]
        assert all(item.candidate_delivery_plan_id is None for item in attempts)
        assert all(item.solver_status.value == "INFEASIBLE" for item in attempts)
        incident = session.get(Incident, incident_id)
        assert incident is not None
        assert incident.incident_type is IncidentType.VEHICLE_UNAVAILABLE
        assert incident.status.value == "REPLANNING"


def test_invalid_result_is_persisted_and_does_not_expand_again(monkeypatch) -> None:
    from app.modules.recovery import deterministic_orchestration as recovery_orchestration

    with recovery_client() as (client, session):
        prepare_route(session)

        def invalid_result(*args, **kwargs) -> tuple[ValidationIssue, ...]:
            return (
                ValidationIssue(
                    code="FORCED_INVALID",
                    message="Forced deterministic validation failure",
                ),
            )

        monkeypatch.setattr(
            recovery_orchestration.SolverResultValidator,
            "validate",
            invalid_result,
        )

        async def scenario() -> tuple[str, httpx.Response]:
            incident_id = await create_incident(client)
            response = await client.post(
                f"/api/incidents/{incident_id}/deterministic-recovery", json={}
            )
            return incident_id, response

        incident_id, response = asyncio.run(scenario())
        assert response.status_code == 500
        assert response.json()["code"] == "RECOVERY_VALIDATION_FAILED"
        session.expire_all()
        attempts = list(
            session.scalars(
                select(RecoveryPlan)
                .where(RecoveryPlan.incident_id == incident_id)
                .order_by(RecoveryPlan.attempt_no)
            )
        )
        assert [item.replanning_scope.value for item in attempts] == [
            "AFFECTED_ROUTE",
            "CROSS_ROUTE",
        ]
        assert attempts[-1].solver_status.value == "FEASIBLE"
        assert attempts[-1].validation_status.value == "INVALID"
        assert attempts[-1].candidate_delivery_plan_id is None


def test_cross_route_uses_active_vehicle_with_onboard_load() -> None:
    active_vehicle_id = "50000000-0000-0000-0000-000000000001"
    onboard_order_id = "40000000-0000-0000-0000-000000000002"
    with recovery_client() as (client, session):
        prepare_route(session)
        active_vehicle = session.get(Vehicle, active_vehicle_id)
        onboard_order = session.get(Order, onboard_order_id)
        assert active_vehicle is not None and onboard_order is not None
        active_vehicle.status = ResourceStatus.ACTIVE
        onboard_order.execution_status = OrderExecutionStatus.PICKED_UP
        session.commit()

        incident_id = asyncio.run(create_incident(client))
        with session.begin():
            context = materialize_recovery_context(
                session,
                incident_id=incident_id,
                scope=ReplanningScope.CROSS_ROUTE,
            )

        active_fact = next(
            item for item in context.vehicles if str(item.vehicle_id) == active_vehicle_id
        )
        onboard_fact = next(
            item for item in context.target_orders if str(item.order_id) == onboard_order_id
        )
        assert active_fact.initial_load_load_units == onboard_order.demand_load_units
        assert onboard_fact.delivery_only is True
        assert onboard_fact.required_vehicle_id == active_fact.vehicle_id

        matrix = build_distance_time_matrix(context.locations)
        solver_input = build_recovery_solver_input(context, matrix)
        solver_vehicle = next(
            item for item in solver_input.vehicles if item.vehicle_id == active_fact.vehicle_id
        )
        solver_order = next(
            item for item in solver_input.orders if item.order_id == onboard_fact.order_id
        )
        assert solver_vehicle.initial_load_load_units == onboard_order.demand_load_units
        assert solver_order.required_vehicle_id == active_fact.vehicle_id


def test_all_remaining_includes_every_incomplete_execution_state() -> None:
    delivering_order_id = "40000000-0000-0000-0000-000000000002"
    with recovery_client() as (client, session):
        prepare_route(session)
        vehicle = session.get(Vehicle, "50000000-0000-0000-0000-000000000001")
        order = session.get(Order, delivering_order_id)
        assert vehicle is not None and order is not None
        vehicle.status = ResourceStatus.ACTIVE
        order.execution_status = OrderExecutionStatus.DELIVERING
        session.commit()

        incident_id = asyncio.run(create_incident(client))
        with session.begin():
            context = materialize_recovery_context(
                session,
                incident_id=incident_id,
                scope=ReplanningScope.ALL_REMAINING,
            )

        fact = next(
            item for item in context.target_orders if str(item.order_id) == delivering_order_id
        )
        assert fact.delivery_only is True
        assert fact.required_vehicle_id == vehicle.id


def test_recovery_repository_refreshes_cached_plan_facts() -> None:
    from sqlalchemy import text
    from app.db.repositories.plan_repository import PlanRepository

    with recovery_client() as (_, session):
        repository = PlanRepository(session)
        plan = repository.get_current_plan_for_operations(
            datetime.fromisoformat(BUSINESS_DATE).date()
        )
        assert plan is not None
        first = repository.get_plan_for_recovery(plan.id)
        assert first is not None
        membership = next(
            item for item in first.plan_orders if str(item.order_id) == ORDER_IDS[2]
        )
        membership.order.risk_status = OrderRiskStatus.NORMAL
        session.flush()
        session.execute(
            text("UPDATE orders SET risk_status = 'AT_RISK' WHERE id = :order_id"),
            {"order_id": ORDER_IDS[2]},
        )

        refreshed = repository.get_plan_for_recovery(plan.id)
        assert refreshed is not None
        refreshed_membership = next(
            item for item in refreshed.plan_orders if str(item.order_id) == ORDER_IDS[2]
        )
        assert refreshed_membership.order.risk_status is OrderRiskStatus.AT_RISK


def test_recovery_rejects_execution_fact_changed_during_solver(monkeypatch) -> None:
    from app.modules.recovery import deterministic_orchestration as recovery_orchestration

    with recovery_client() as (client, session):
        prepare_route(session)
        original_solve = recovery_orchestration.ORToolsSolver.solve
        changed = False

        def solve_then_change_order(solver, solver_input):
            nonlocal changed
            result = original_solve(solver, solver_input)
            if (
                solver_input.recovery_scope == "CROSS_ROUTE"
                and result.status is SolverStatus.FEASIBLE
                and not changed
            ):
                assert any(str(item.order_id) == ORDER_IDS[2] for item in solver_input.orders)
                assert not session.in_transaction()
                with session.begin():
                    order = session.get(Order, ORDER_IDS[2])
                    assert order is not None
                    order.risk_status = (
                        OrderRiskStatus.NORMAL
                        if order.risk_status is OrderRiskStatus.AT_RISK
                        else OrderRiskStatus.AT_RISK
                    )
                changed = True
            return result

        monkeypatch.setattr(
            recovery_orchestration.ORToolsSolver,
            "solve",
            solve_then_change_order,
        )

        async def scenario() -> tuple[str, httpx.Response]:
            incident_id = await create_incident(client)
            return incident_id, await client.post(
                f"/api/incidents/{incident_id}/deterministic-recovery", json={}
            )

        incident_id, response = asyncio.run(scenario())
        assert changed
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "RECOVERY_CONTEXT_CHANGED"
        session.expire_all()
        attempts = list(
            session.scalars(
                select(RecoveryPlan).where(RecoveryPlan.incident_id == incident_id)
            )
        )
        assert attempts
        assert all(item.candidate_delivery_plan_id is None for item in attempts)


def test_recovery_context_uses_operational_time_after_detection(monkeypatch) -> None:
    from app.modules.recovery import deterministic_context as recovery_context

    with recovery_client() as (client, session):
        prepare_route(session)
        incident_id = asyncio.run(create_incident(client))
        monkeypatch.setattr(
            recovery_context,
            "_now",
            lambda: datetime.fromisoformat("2026-09-25T10:25:00+08:00"),
            raising=False,
        )
        with session.begin():
            context = materialize_recovery_context(
                session,
                incident_id=incident_id,
                scope=ReplanningScope.AFFECTED_ROUTE,
            )
        assert context.current_time == datetime.fromisoformat(
            "2026-09-25T10:25:00+08:00"
        )


def test_candidate_preserves_started_route_status_and_start_time() -> None:
    active_vehicle_id = "50000000-0000-0000-0000-000000000001"
    with recovery_client() as (client, session):
        prepare_route(session)
        vehicle = session.get(Vehicle, active_vehicle_id)
        assert vehicle is not None
        vehicle.status = ResourceStatus.ACTIVE
        session.commit()

        async def scenario() -> httpx.Response:
            incident_id = await create_incident(client)
            return await client.post(f"/api/incidents/{incident_id}/deterministic-recovery", json={})

        response = asyncio.run(scenario())
        assert response.status_code == 201, response.text
        candidate_id = response.json()["data"]["candidate_delivery_plan_id"]
        assert candidate_id is not None
        session.expire_all()
        route = session.scalar(
            select(VehicleRoute).where(
                VehicleRoute.delivery_plan_id == candidate_id,
                VehicleRoute.vehicle_id == active_vehicle_id,
            )
        )
        assert route is not None
        assert route.status.value == "ACTIVE"
        assert route.actual_start_at == datetime.fromisoformat(
            "2026-09-25T08:58:00+08:00"
        )
