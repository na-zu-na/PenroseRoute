"""PostgreSQL-backed P0 acceptance scenarios; every scenario rolls back its demo data."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import Principal
from app.api.dependencies import get_db
from app.api.routes.decisions import get_decision_service
from app.api.routes.recovery import require_operations_user
from app.db.models import (
    Customer, DeliveryPlan, DeliveryPlanOrder, Driver, Incident,
    IncidentAffectedOrder, Location, Merchant, Order, RecoveryPlan,
    RouteStop, Vehicle, VehicleDriverAssignment, VehicleRoute,
)
from app.db.models.fleet import AssignmentStatus, ResourceStatus
from app.db.models.planning import DeliveryPlanStatus, StopStatus, StopType
from app.db.models.resources import OrderExecutionStatus
from app.db.session import engine
from app.main import app
from app.modules.decisions.service import DeterministicDecisionService


@contextmanager
def demo_client(clock) -> Iterator[tuple[TestClient, object]]:
    connection = engine.connect()
    outer_transaction = connection.begin()
    previous_overrides = app.dependency_overrides.copy()

    def sessions():
        return Session(
            bind=connection, autoflush=False, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    def get_demo_db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = get_demo_db
    app.dependency_overrides[require_operations_user] = lambda: Principal("p0-dispatcher", "dispatcher")
    app.dependency_overrides[get_decision_service] = lambda: DeterministicDecisionService(
        sessions, clock=clock,
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        outer_transaction.rollback()
        connection.close()


def seed_demo(sessions, business_date: date):
    suffix = uuid4().hex[:8]
    day = datetime.combine(business_date, datetime.min.time(), timezone.utc)
    with sessions() as session, session.begin():
        hub = Location(location_code=f"E2E-HUB-{suffix}", display_name="Demo Hub", latitude=1.3000, longitude=103.8000)
        pickup = Location(location_code=f"E2E-PICK-{suffix}", display_name="Demo Merchant", latitude=1.3010, longitude=103.8010)
        deliveries = [
            Location(location_code=f"E2E-D{i}-{suffix}", display_name=f"Demo Customer {i}",
                     latitude=1.3020 + i * .001, longitude=103.8020 + i * .001)
            for i in range(3)
        ]
        merchants = [
            Merchant(merchant_code=f"E2E-M{i}-{suffix}", name=f"Demo Merchant {i}", pickup_location=pickup,
                     operational_ready_at=day + timedelta(hours=8 + i), default_pickup_service_seconds=60)
            for i in range(3)
        ]
        customers = [
            Customer(customer_code=f"E2E-C{i}-{suffix}", name=f"Demo Customer {i}", default_delivery_location=deliveries[i])
            for i in range(3)
        ]
        orders = [
            Order(order_code=f"E2E-O{i}-{suffix}", business_date=business_date, merchant=merchants[i],
                  customer=customers[i], pickup_location=pickup, delivery_location=deliveries[i],
                  pickup_ready_at=day + timedelta(hours=8 + i), pickup_service_seconds=60,
                  delivery_window_start_at=day + timedelta(hours=8 + i, minutes=15),
                  delivery_window_end_at=day + timedelta(hours=8 + i, minutes=50),
                  delivery_service_seconds=60, demand_load_units=1,
                  execution_status=OrderExecutionStatus.PLANNED)
            for i in range(3)
        ]
        primary = Vehicle(vehicle_code=f"E2E-V1-{suffix}", name="Demo Primary", capacity_load_units=4,
                          status=ResourceStatus.AVAILABLE, current_location=hub, current_location_recorded_at=day + timedelta(hours=8))
        spare = Vehicle(vehicle_code=f"E2E-V2-{suffix}", name="Demo Spare", capacity_load_units=4,
                        status=ResourceStatus.UNAVAILABLE, current_location=hub, current_location_recorded_at=day + timedelta(hours=8))
        primary_driver = Driver(driver_code=f"E2E-DR1-{suffix}", name="Primary Driver", status=ResourceStatus.AVAILABLE)
        spare_driver = Driver(driver_code=f"E2E-DR2-{suffix}", name="Spare Driver", status=ResourceStatus.AVAILABLE)
        assignments = [
            VehicleDriverAssignment(vehicle=vehicle, driver=driver,
                                    assigned_from_at=day + timedelta(hours=8),
                                    assigned_until_at=day + timedelta(hours=14), status=AssignmentStatus.PLANNED)
            for vehicle, driver in ((primary, primary_driver), (spare, spare_driver))
        ]
        session.add_all([*orders, *assignments])
        session.flush()
        ids = {
            "orders": tuple(order.id for order in orders),
            "merchants": tuple(merchant.id for merchant in merchants),
            "primary": primary.id,
            "spare": spare.id,
        }
    return ids


def _post_ok(client: TestClient, path: str, body: dict, status: int = 200, code: str | None = None) -> dict:
    response = client.post(path, json=body)
    assert response.status_code == status, f"{path}: {response.text}"
    assert response.json()["success"] is True
    assert response.json()["request_id"]
    if code is not None:
        assert response.json()["code"] == code
    return response.json()["data"]


def test_vehicle_breakdown_from_new_plan_through_dispatcher_approval(monkeypatch):
    from app.modules.recovery import deterministic_context

    business_date = date(2026, 10, 2)
    clock = {"now": datetime(2026, 10, 2, 9, 10, tzinfo=timezone.utc)}
    monkeypatch.setattr(deterministic_context, "_now", lambda: clock["now"])
    with demo_client(lambda: clock["now"] + timedelta(minutes=1)) as (client, sessions):
        ids = seed_demo(sessions, business_date)
        normal = _post_ok(client, "/api/planning/generate", {"business_date": business_date.isoformat()}, 201, "PLAN_CREATED")
        base_id = normal["delivery_plan_id"]

        with sessions() as session:
            base = session.get(DeliveryPlan, base_id)
            memberships = list(session.scalars(select(DeliveryPlanOrder).where(DeliveryPlanOrder.delivery_plan_id == base.id)))
            routes = list(session.scalars(select(VehicleRoute).where(VehicleRoute.delivery_plan_id == base.id)))
            assert base.status is DeliveryPlanStatus.CURRENT
            assert {item.order_id for item in memberships} == set(ids["orders"])
            assert len(routes) == 1 and routes[0].vehicle_id == ids["primary"]
            stops = list(session.scalars(select(RouteStop).where(RouteStop.vehicle_route_id == routes[0].id).order_by(RouteStop.sequence_no)))
            assert len(stops) == 6
            assert [stop.stop_type for stop in stops[:3]] == [StopType.PICKUP, StopType.DELIVERY, StopType.PICKUP]
            stop_data = [(stop.id, stop.planned_arrival_at) for stop in stops[:3]]

        last_event = datetime(2026, 10, 2, 8, tzinfo=timezone.utc)
        for stop_id, planned_arrival in stop_data:
            arrival = max(last_event + timedelta(seconds=1), planned_arrival)
            for action, event in (
                ("arrive", arrival),
                ("start-service", arrival + timedelta(seconds=1)),
                ("complete", arrival + timedelta(seconds=60)),
            ):
                _post_ok(client, f"/api/route-stops/{stop_id}/{action}", {"occurred_at": event.isoformat()})
            last_event = arrival + timedelta(seconds=60)
        clock["now"] = last_event + timedelta(minutes=2)
        assert client.get(f"/api/vehicles/{ids['primary']}").json()["data"]["status"] == "ACTIVE"
        with sessions() as session, session.begin():
            session.get(Vehicle, ids["spare"]).status = ResourceStatus.AVAILABLE

        incident = _post_ok(client, "/api/incidents/vehicle-unavailable", {
            "business_date": business_date.isoformat(), "vehicle_id": str(ids["primary"]),
            "location": {"location_code": f"E2E-BREAK-{uuid4().hex[:8]}", "address_text": "Demo breakdown",
                         "latitude": 1.3015, "longitude": 103.8015},
            "detected_at": clock["now"].isoformat(),
        }, 201, "VEHICLE_INCIDENT_CREATED")
        incident_id = incident["incident_id"]
        incident_detail = client.get(f"/api/incidents/{incident_id}")
        assert incident_detail.status_code == 200, incident_detail.text
        assert incident_detail.json()["data"]["base_delivery_plan_id"] == str(base_id)
        assert incident_detail.json()["data"]["status"] == "REPLANNING"
        affected_response = client.get(f"/api/incidents/{incident_id}/affected-orders")
        assert affected_response.status_code == 200, affected_response.text
        assert {item["impact_type"] for item in affected_response.json()["data"]} == {
            "COMPLETED_FROZEN", "HANDOVER_REQUIRED", "PICKUP_REPLAN",
        }
        assert client.get(f"/api/vehicles/{ids['primary']}").json()["data"]["status"] == "UNAVAILABLE"
        with sessions() as session:
            impacts = list(session.scalars(select(IncidentAffectedOrder).where(IncidentAffectedOrder.incident_id == incident_id)))
            assert {item.impact_type.value for item in impacts} == {
                "COMPLETED_FROZEN", "HANDOVER_REQUIRED", "PICKUP_REPLAN",
            }
            frozen = next(item for item in impacts if item.order_id == ids["orders"][0])
            handover_impact = next(item for item in impacts if item.order_id == ids["orders"][1])
            assert frozen.was_completed and not frozen.requires_replanning
            assert handover_impact.was_picked_up and handover_impact.handover_required
            assert session.get(Order, ids["orders"][0]).execution_status is OrderExecutionStatus.COMPLETED
            assert session.get(Order, ids["orders"][1]).execution_status is OrderExecutionStatus.PICKED_UP
            assert session.get(Order, ids["orders"][2]).execution_status is OrderExecutionStatus.PLANNED

        recovery = _post_ok(client, f"/api/incidents/{incident_id}/recovery", {}, 201, "RECOVERY_PENDING_REVIEW")
        candidate_id = recovery["candidate_delivery_plan_id"]
        assert recovery["outcome"] == "PENDING_REVIEW"
        assert [item["replanning_scope"] for item in recovery["attempts_created"]] == [
            "AFFECTED_ROUTE", "CROSS_ROUTE",
        ]
        assert recovery["attempts_created"][0]["solver_status"] == "INFEASIBLE"
        attempts_response = client.get(f"/api/incidents/{incident_id}/recovery-plans")
        assert attempts_response.status_code == 200, attempts_response.text
        attempts = attempts_response.json()["data"]
        assert [(item["attempt_no"], item["solver_status"]) for item in attempts] == [
            (1, "INFEASIBLE"), (2, "FEASIBLE"),
        ]
        assert attempts[0]["candidate_delivery_plan_id"] is None
        assert client.get(f"/api/recovery-plans/{recovery['reviewable_recovery_plan_id']}").json()["data"]["status"] == "PENDING_REVIEW"
        assert client.get(f"/api/delivery-plans/{base_id}").json()["data"]["status"] == "CURRENT"
        assert client.get(f"/api/delivery-plans/{candidate_id}").json()["data"]["status"] == "CANDIDATE"
        with sessions() as session:
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT
            assert session.get(DeliveryPlan, candidate_id).status is DeliveryPlanStatus.CANDIDATE
            candidate_stops = list(session.scalars(
                select(RouteStop).join(VehicleRoute).where(VehicleRoute.delivery_plan_id == candidate_id)
            ))
            handover = next(stop for stop in candidate_stops if stop.stop_type is StopType.HANDOVER)
            delivery = next(stop for stop in candidate_stops if stop.order_id == ids["orders"][1] and stop.stop_type is StopType.DELIVERY)
            assert handover.order_id == ids["orders"][1]
            assert handover.location_id == session.get(Incident, incident_id).incident_location_id
            assert session.get(VehicleRoute, handover.vehicle_route_id).vehicle_id == ids["spare"]
            assert delivery.precedence_stop_id == handover.id
            assert any(stop.status is StopStatus.COMPLETED and stop.order_id == ids["orders"][0] for stop in candidate_stops)

        _post_ok(client, f"/api/recovery-plans/{recovery['reviewable_recovery_plan_id']}/approve",
                 {"decision_reason": "Demo handover accepted"}, code="RECOVERY_APPROVED")
        assert client.get(f"/api/delivery-plans/{base_id}").json()["data"]["status"] == "SUPERSEDED"
        assert client.get("/api/delivery-plans/current", params={"business_date": business_date.isoformat()}).json()["data"]["id"] == str(candidate_id)
        assert client.get(f"/api/incidents/{incident_id}").json()["data"]["status"] == "RESOLVED"
        with sessions() as session:
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.SUPERSEDED
            assert session.get(DeliveryPlan, candidate_id).status is DeliveryPlanStatus.CURRENT
            assert session.get(Incident, incident_id).status.value == "RESOLVED"
            assert session.get(Order, ids["orders"][0]).execution_status is OrderExecutionStatus.COMPLETED


@pytest.mark.parametrize(
    ("decision", "business_date"),
    [("reject", date(2026, 10, 3)), ("modify", date(2026, 10, 4))],
)
def test_merchant_delay_from_new_plan_through_dispatcher_decision(monkeypatch, decision, business_date):
    from app.modules.recovery import deterministic_context

    day = datetime.combine(business_date, datetime.min.time(), timezone.utc)
    clock = {"now": day + timedelta(hours=8, minutes=5)}
    monkeypatch.setattr(deterministic_context, "_now", lambda: clock["now"])
    with demo_client(lambda: clock["now"] + timedelta(minutes=1)) as (client, sessions):
        ids = seed_demo(sessions, business_date)
        normal = _post_ok(client, "/api/planning/generate", {"business_date": business_date.isoformat()}, 201)
        base_id = normal["delivery_plan_id"]
        incident = _post_ok(client, "/api/incidents/merchant-delay", {
            "business_date": business_date.isoformat(), "merchant_id": str(ids["merchants"][0]),
            "updated_ready_at": (day + timedelta(hours=8, minutes=15)).isoformat(),
            "detected_at": clock["now"].isoformat(),
        }, 201, "MERCHANT_DELAY_ASSESSED")
        assert incident["delay_seconds"] == 900
        assert incident["requires_replanning"] is True
        incident_id = incident["incident_id"]
        assert client.get(f"/api/incidents/{incident_id}").json()["data"]["delay_seconds"] == 900
        assert client.get(f"/api/incidents/{incident_id}/affected-orders").json()["data"]
        with sessions() as session:
            recorded = session.get(Incident, incident_id)
            assert recorded.status.value == "REPLANNING"
            assert recorded.original_ready_at == day + timedelta(hours=8)
            assert recorded.updated_ready_at == day + timedelta(hours=8, minutes=15)
            assert session.get(Order, ids["orders"][0]).execution_status is OrderExecutionStatus.PLANNED

        recovery = _post_ok(client, f"/api/incidents/{incident_id}/recovery", {}, 201, "RECOVERY_PENDING_REVIEW")
        assert recovery["outcome"] == "PENDING_REVIEW"
        candidate_id = recovery["candidate_delivery_plan_id"]
        assert client.get(f"/api/recovery-plans/{recovery['reviewable_recovery_plan_id']}").json()["data"]["candidate_delivery_plan_id"] == str(candidate_id)
        with sessions() as session:
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT
            assert session.get(DeliveryPlan, candidate_id).status is DeliveryPlanStatus.CANDIDATE
        result = _post_ok(
            client, f"/api/recovery-plans/{recovery['reviewable_recovery_plan_id']}/{decision}",
            {"decision_reason": f"Demo {decision}"},
            code="RECOVERY_REJECTED" if decision == "reject" else "RECOVERY_MODIFICATION_PROCESSED",
        )
        assert client.get(f"/api/delivery-plans/{candidate_id}").json()["data"]["status"] == "CANCELLED"
        attempts = client.get(f"/api/incidents/{incident_id}/recovery-plans").json()["data"]
        assert attempts[0]["dispatcher_decision"] == decision.upper()
        if decision == "modify":
            assert [item["replanning_scope"] for item in attempts] == ["AFFECTED_ROUTE", "CROSS_ROUTE"]
            assert attempts[1]["previous_recovery_plan_id"] == attempts[0]["recovery_plan_id"]
        with sessions() as session:
            assert session.get(DeliveryPlan, base_id).status is DeliveryPlanStatus.CURRENT
            assert session.get(DeliveryPlan, candidate_id).status is DeliveryPlanStatus.CANCELLED
            old_recovery = session.get(RecoveryPlan, recovery["reviewable_recovery_plan_id"])
            assert old_recovery.dispatcher_decision.value == decision.upper()
            if decision == "reject":
                assert session.get(Incident, incident_id).status.value == "ASSESSING"
            else:
                new_recovery = session.get(RecoveryPlan, result["new_recovery_plan_id"])
                assert new_recovery.previous_recovery_plan_id == old_recovery.id
                assert new_recovery.status.value == "PENDING_REVIEW"
                assert new_recovery.replanning_scope.value == "CROSS_ROUTE"
                assert session.get(DeliveryPlan, new_recovery.candidate_delivery_plan_id).status is DeliveryPlanStatus.CANDIDATE


def test_merchant_delay_at_ten_minutes_resolves_without_recovery(monkeypatch):
    from app.modules.recovery import deterministic_context

    business_date = date(2026, 10, 5)
    day = datetime(2026, 10, 5, tzinfo=timezone.utc)
    clock = day + timedelta(hours=8, minutes=5)
    monkeypatch.setattr(deterministic_context, "_now", lambda: clock)
    with demo_client(lambda: clock) as (client, sessions):
        ids = seed_demo(sessions, business_date)
        normal = _post_ok(client, "/api/planning/generate", {"business_date": business_date.isoformat()}, 201)
        before_rows = client.get("/api/operations/orders", params={"business_date": business_date.isoformat()}).json()["data"]["items"]
        before = next(item for item in before_rows if item["order_id"] == str(ids["orders"][0]))
        incident = _post_ok(client, "/api/incidents/merchant-delay", {
            "business_date": business_date.isoformat(), "merchant_id": str(ids["merchants"][0]),
            "updated_ready_at": (day + timedelta(hours=8, minutes=10)).isoformat(),
            "detected_at": clock.isoformat(),
        }, 201)
        assert incident["delay_seconds"] == 600
        assert incident["requires_replanning"] is False
        detail = client.get(f"/api/incidents/{incident['incident_id']}").json()["data"]
        assert detail["status"] == "RESOLVED"
        assert detail["recovery_attempts"] == []
        assert client.get(f"/api/incidents/{incident['incident_id']}/recovery-plans").json()["data"] == []
        refused = client.post(f"/api/incidents/{incident['incident_id']}/recovery", json={})
        assert refused.status_code == 409
        assert refused.json()["code"] == "RECOVERY_NOT_REQUIRED"
        after_rows = client.get("/api/operations/orders", params={"business_date": business_date.isoformat()}).json()["data"]["items"]
        after = next(item for item in after_rows if item["order_id"] == str(ids["orders"][0]))
        assert datetime.fromisoformat(after["delivery_eta"]) > datetime.fromisoformat(before["delivery_eta"])
        assert after["risk_status"] in {"NORMAL", "AT_RISK"}
        with sessions() as session:
            assert session.get(Incident, incident["incident_id"]).status.value == "RESOLVED"
            assert session.get(DeliveryPlan, normal["delivery_plan_id"]).status is DeliveryPlanStatus.CURRENT
            assert list(session.scalars(select(RecoveryPlan).where(RecoveryPlan.incident_id == incident["incident_id"]))) == []


@pytest.mark.parametrize(
    ("failure_kind", "expected_code"),
    [("ERROR", "RECOVERY_SOLVER_ERROR"), ("INVALID", "RECOVERY_VALIDATION_FAILED")],
)
def test_generated_plan_recovery_failure_does_not_expand_scope(
    monkeypatch, failure_kind, expected_code
):
    from app.integrations.optimization.contracts import SolverResult, SolverStatus
    from app.integrations.optimization.result_validator import ValidationIssue
    from app.modules.recovery import deterministic_context, deterministic_orchestration

    business_date = date(2026, 10, 6 if failure_kind == "ERROR" else 7)
    day = datetime.combine(business_date, datetime.min.time(), timezone.utc)
    clock = day + timedelta(hours=8, minutes=5)
    monkeypatch.setattr(deterministic_context, "_now", lambda: clock)
    with demo_client(lambda: clock) as (client, sessions):
        ids = seed_demo(sessions, business_date)
        normal = _post_ok(client, "/api/planning/generate", {"business_date": business_date.isoformat()}, 201)
        incident = _post_ok(client, "/api/incidents/merchant-delay", {
            "business_date": business_date.isoformat(), "merchant_id": str(ids["merchants"][0]),
            "updated_ready_at": (day + timedelta(hours=8, minutes=15)).isoformat(),
            "detected_at": clock.isoformat(),
        }, 201)
        if failure_kind == "ERROR":
            real_solve = deterministic_orchestration.ORToolsSolver.solve

            def solve_then_inject_error(solver, solver_input):
                # Exercise the real optimizer, then simulate its rare execution-error result.
                assert real_solve(solver, solver_input).status is SolverStatus.FEASIBLE
                return SolverResult(
                    status=SolverStatus.ERROR, routes=(), unassigned_orders=(),
                    total_distance_meters=0, total_duration_seconds=0,
                    diagnostic="Injected solver failure",
                )

            monkeypatch.setattr(
                deterministic_orchestration.ORToolsSolver, "solve",
                solve_then_inject_error,
            )
        else:
            monkeypatch.setattr(
                deterministic_orchestration.SolverResultValidator, "validate",
                lambda *_: (ValidationIssue(code="INJECTED_INVALID", message="Injected validation failure"),),
            )
        response = client.post(f"/api/incidents/{incident['incident_id']}/recovery", json={})
        assert response.status_code == 500
        assert response.json()["code"] == expected_code
        assert response.json()["success"] is False
        assert response.json()["request_id"]
        attempts_response = client.get(f"/api/incidents/{incident['incident_id']}/recovery-plans")
        assert attempts_response.status_code == 200, attempts_response.text
        assert [(item["attempt_no"], item["replanning_scope"]) for item in attempts_response.json()["data"]] == [(1, "AFFECTED_ROUTE")]
        assert attempts_response.json()["data"][0]["candidate_delivery_plan_id"] is None
        with sessions() as session:
            attempts = list(session.scalars(
                select(RecoveryPlan).where(RecoveryPlan.incident_id == incident["incident_id"])
            ))
            assert len(attempts) == 1
            assert attempts[0].replanning_scope.value == "AFFECTED_ROUTE"
            assert attempts[0].candidate_delivery_plan_id is None
            assert attempts[0].status.value == "DRAFT"
            assert attempts[0].solver_status.value == ("ERROR" if failure_kind == "ERROR" else "FEASIBLE")
            assert (attempts[0].validation_status.value if attempts[0].validation_status else None) == (
                None if failure_kind == "ERROR" else "INVALID"
            )
            assert session.get(DeliveryPlan, normal["delivery_plan_id"]).status is DeliveryPlanStatus.CURRENT
            assert session.get(Incident, incident["incident_id"]).status.value == "REPLANNING"
