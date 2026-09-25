import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models import Incident, IncidentAffectedOrder, Order, RecoveryPlan, Vehicle
from app.db.models.fleet import ResourceStatus
from app.db.models.recovery import ImpactType, IncidentStatus, IncidentType
from app.db.models.resources import OrderExecutionStatus, OrderRiskStatus
from app.db.session import engine
from app.main import app


BUSINESS_DATE = "2026-09-25"
VEHICLE_ID = "50000000-0000-0000-0000-000000000002"
ORDER_IDS = (
    "40000000-0000-0000-0000-000000000003",
    "40000000-0000-0000-0000-000000000004",
    "40000000-0000-0000-0000-000000000005",
)


@contextmanager
def incident_client() -> Iterator[tuple[httpx.AsyncClient, Session]]:
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


def prepare_vehicle_route(session: Session) -> None:
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


def test_vehicle_unavailable_creates_deterministic_snapshots_atomically() -> None:
    with incident_client() as (client, session):
        prepare_vehicle_route(session)

        async def scenario() -> None:
            response = await client.post(
                "/api/incidents/vehicle-unavailable",
                json={
                    "business_date": BUSINESS_DATE,
                    "vehicle_id": VEHICLE_ID,
                    "location": {
                        "location_code": "T12-BREAKDOWN-001",
                        "address_text": "Test breakdown point",
                        "latitude": 1.305,
                        "longitude": 103.755,
                    },
                    "detected_at": "2026-09-25T10:05:00+08:00",
                },
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["code"] == "VEHICLE_INCIDENT_CREATED"
            assert body["data"]["status"] == "REPLANNING"
            assert body["data"]["business_date"] == BUSINESS_DATE
            assert body["data"]["affected_order_count"] == 3
            assert body["data"]["handover_order_count"] == 1
            assert body["data"]["replanning_scope"] == "AFFECTED_ROUTE"
            assert body["data"]["recovery_required"] is True
            assert body["data"]["breakdown_location"]["address_text"] == (
                "Test breakdown point"
            )

            duplicate = await client.post(
                "/api/incidents/vehicle-unavailable",
                json={
                    "business_date": BUSINESS_DATE,
                    "vehicle_id": VEHICLE_ID,
                    "detected_at": "2026-09-25T10:06:00+08:00",
                },
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["code"] == "INCIDENT_ALREADY_EXISTS"

        asyncio.run(scenario())

        session.expire_all()
        incident = session.scalar(
            select(Incident).where(
                Incident.vehicle_id == VEHICLE_ID,
                Incident.incident_type == IncidentType.VEHICLE_UNAVAILABLE,
            )
        )
        assert incident is not None
        assert incident.status is IncidentStatus.REPLANNING
        impacts = list(
            session.scalars(
                select(IncidentAffectedOrder)
                .where(IncidentAffectedOrder.incident_id == incident.id)
                .order_by(IncidentAffectedOrder.order_id)
            )
        )
        assert [impact.impact_type for impact in impacts] == [
            ImpactType.COMPLETED_FROZEN,
            ImpactType.HANDOVER_REQUIRED,
            ImpactType.PICKUP_REPLAN,
        ]
        assert impacts[0].was_completed and not impacts[0].requires_replanning
        assert impacts[1].was_picked_up and impacts[1].handover_required
        assert impacts[1].risk_status_snapshot is OrderRiskStatus.AT_RISK
        assert not impacts[2].was_picked_up and impacts[2].requires_replanning
        vehicle = session.get(Vehicle, VEHICLE_ID)
        assert vehicle is not None and vehicle.status is ResourceStatus.UNAVAILABLE
        assert session.scalar(
            select(RecoveryPlan).where(RecoveryPlan.incident_id == incident.id)
        ) is None


def test_vehicle_status_active_to_unavailable_delegates_to_incident_workflow() -> None:
    with incident_client() as (client, session):
        prepare_vehicle_route(session)

        async def scenario() -> None:
            response = await client.patch(
                f"/api/vehicles/{VEHICLE_ID}/status",
                json={
                    "status": "UNAVAILABLE",
                    "business_date": BUSINESS_DATE,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["vehicle"]["status"] == "UNAVAILABLE"
            assert response.json()["data"]["incident"]["incident_type"] == (
                "VEHICLE_UNAVAILABLE"
            )

        asyncio.run(scenario())


def test_vehicle_incident_requires_business_date_context_and_current_route() -> None:
    with incident_client() as (client, session):
        prepare_vehicle_route(session)

        async def scenario() -> None:
            missing_plan = await client.post(
                "/api/incidents/vehicle-unavailable",
                json={
                    "business_date": "2030-01-01",
                    "vehicle_id": VEHICLE_ID,
                    "detected_at": "2030-01-01T10:00:00+08:00",
                },
            )
            assert missing_plan.status_code == 404
            assert missing_plan.json()["code"] == "CURRENT_PLAN_NOT_FOUND"

            not_on_route = await client.post(
                "/api/incidents/vehicle-unavailable",
                json={
                    "business_date": BUSINESS_DATE,
                    "vehicle_id": "50000000-0000-0000-0000-000000000003",
                    "detected_at": "2026-09-25T10:05:00+08:00",
                },
            )
            assert not_on_route.status_code == 409
            assert not_on_route.json()["code"] == "VEHICLE_NOT_EXECUTING_ROUTE"

            stale_fact = await client.post(
                "/api/incidents/vehicle-unavailable",
                json={
                    "business_date": BUSINESS_DATE,
                    "vehicle_id": VEHICLE_ID,
                    "location": {
                        "location_code": "T12-STALE-BREAKDOWN",
                        "latitude": 1.3,
                        "longitude": 103.7,
                    },
                    "detected_at": "2026-09-25T09:00:00+08:00",
                },
            )
            assert stale_fact.status_code == 409
            assert stale_fact.json()["code"] == "INCIDENT_FACT_CONFLICT"

        asyncio.run(scenario())
