import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.db.models import Incident, IncidentAffectedOrder, Merchant, Order, RecoveryPlan, RouteStop
from app.db.models.resources import OrderExecutionStatus
from app.db.models.recovery import ImpactType, IncidentStatus, IncidentType
from app.db.models.resources import MerchantPreparationStatus, OrderRiskStatus
from app.db.session import engine
from app.main import app


BUSINESS_DATE = "2026-09-25"
MERCHANT_ID = "20000000-0000-0000-0000-000000000001"
DIRECT_ORDER_ID = "40000000-0000-0000-0000-000000000005"
DOWNSTREAM_ORDER_ID = "40000000-0000-0000-0000-000000000004"
PICKUP_STOP_ID = "c0000000-0000-0000-0000-000000000008"
DELIVERY_STOP_ID = "c0000000-0000-0000-0000-000000000010"


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


def prepare_merchant_delay(session: Session) -> None:
    merchant = session.get(Merchant, MERCHANT_ID)
    pickup = session.get(RouteStop, PICKUP_STOP_ID)
    direct_order = session.get(Order, DIRECT_ORDER_ID)
    downstream_order = session.get(Order, DOWNSTREAM_ORDER_ID)
    assert merchant and pickup and direct_order and downstream_order
    merchant.operational_ready_at = datetime.fromisoformat("2026-09-25T09:00:00+08:00")
    merchant.preparation_status = MerchantPreparationStatus.READY
    pickup.time_window_start_at = datetime.fromisoformat("2026-09-25T10:00:00+08:00")
    direct_order.risk_status = OrderRiskStatus.NORMAL
    downstream_order.risk_status = OrderRiskStatus.NORMAL
    session.commit()


def test_explicit_merchant_delay_over_threshold_creates_replanning_impact() -> None:
    with incident_client() as (client, session):
        prepare_merchant_delay(session)

        async def scenario() -> None:
            response = await client.post(
                "/api/incidents/merchant-delay",
                json={
                    "business_date": BUSINESS_DATE,
                    "merchant_id": MERCHANT_ID,
                    "updated_ready_at": "2026-09-25T10:11:00+08:00",
                    "detected_at": "2026-09-25T09:30:00+08:00",
                },
            )
            assert response.status_code == 201, response.text
            data = response.json()["data"]
            assert data["status"] == "REPLANNING"
            assert data["original_ready_at"] == "2026-09-25T10:00:00+08:00"
            assert data["delay_seconds"] == 660
            assert data["requires_replanning"] is True
            assert data["affected_order_count"] == 2
            assert data["replanning_scope"] == "AFFECTED_ROUTE"

            duplicate = await client.post(
                "/api/incidents/merchant-delay",
                json={
                    "business_date": BUSINESS_DATE,
                    "merchant_id": MERCHANT_ID,
                    "updated_ready_at": "2026-09-25T10:12:00+08:00",
                },
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["code"] == "INCIDENT_ALREADY_EXISTS"

        asyncio.run(scenario())

        session.expire_all()
        incident = session.scalar(
            select(Incident).where(
                Incident.merchant_id == MERCHANT_ID,
                Incident.incident_type == IncidentType.MERCHANT_DELAY,
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
            ImpactType.DOWNSTREAM_ROUTE_IMPACT,
            ImpactType.WAITING_TIME_UPDATE,
        ]
        assert all(impact.requires_replanning for impact in impacts)
        merchant = session.get(Merchant, MERCHANT_ID)
        assert merchant is not None
        assert merchant.preparation_status is MerchantPreparationStatus.DELAYED
        assert session.scalar(
            select(RecoveryPlan).where(RecoveryPlan.incident_id == incident.id)
        ) is None


def test_ready_time_endpoint_resolves_ten_minute_delay_and_updates_risk() -> None:
    with incident_client() as (client, session):
        prepare_merchant_delay(session)
        delivery = session.get(RouteStop, DELIVERY_STOP_ID)
        pickup = session.get(RouteStop, PICKUP_STOP_ID)
        assert pickup is not None
        assert delivery is not None
        pickup.time_window_start_at = datetime.fromisoformat("2026-09-25T10:40:00+08:00")
        pickup.planned_arrival_at = datetime.fromisoformat("2026-09-25T10:45:00+08:00")
        pickup.planned_departure_at = datetime.fromisoformat("2026-09-25T10:50:00+08:00")
        delivery.planned_arrival_at = datetime.fromisoformat("2026-09-25T12:20:00+08:00")
        delivery.planned_departure_at = datetime.fromisoformat("2026-09-25T12:25:00+08:00")
        session.commit()

        async def scenario() -> None:
            response = await client.patch(
                f"/api/merchants/{MERCHANT_ID}/ready-time",
                json={
                    "business_date": BUSINESS_DATE,
                    "updated_ready_at": "2026-09-25T10:50:00+08:00",
                    "detected_at": "2026-09-25T09:30:00+08:00",
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["preparation_status"] == "DELAYED"

        asyncio.run(scenario())

        session.expire_all()
        incident = session.scalar(
            select(Incident).where(
                Incident.merchant_id == MERCHANT_ID,
                Incident.incident_type == IncidentType.MERCHANT_DELAY,
            )
        )
        assert incident is not None
        assert incident.status is IncidentStatus.RESOLVED
        assert incident.delay_seconds == 600
        assert incident.resolved_at is not None
        impacts = list(
            session.scalars(
                select(IncidentAffectedOrder).where(
                    IncidentAffectedOrder.incident_id == incident.id
                )
            )
        )
        assert len(impacts) == 2
        assert not any(impact.requires_replanning for impact in impacts)
        order = session.get(Order, DIRECT_ORDER_ID)
        assert order is not None
        assert order.risk_status is OrderRiskStatus.AT_RISK
        delivery = session.get(RouteStop, DELIVERY_STOP_ID)
        assert delivery is not None
        assert delivery.planned_arrival_at == datetime.fromisoformat(
            "2026-09-25T12:25:00+08:00"
        )
        assert delivery.planned_departure_at == datetime.fromisoformat(
            "2026-09-25T12:30:00+08:00"
        )


def test_short_delay_absorbed_by_pickup_slack_does_not_shift_eta() -> None:
    with incident_client() as (client, session):
        prepare_merchant_delay(session)
        delivery = session.get(RouteStop, DELIVERY_STOP_ID)
        assert delivery is not None
        original_arrival = delivery.planned_arrival_at
        original_departure = delivery.planned_departure_at
        session.commit()

        async def scenario() -> None:
            response = await client.patch(
                f"/api/merchants/{MERCHANT_ID}/ready-time",
                json={
                    "business_date": BUSINESS_DATE,
                    "updated_ready_at": "2026-09-25T10:10:00+08:00",
                    "detected_at": "2026-09-25T09:30:00+08:00",
                },
            )
            assert response.status_code == 200, response.text

        asyncio.run(scenario())
        session.expire_all()
        delivery = session.get(RouteStop, DELIVERY_STOP_ID)
        assert delivery is not None
        assert delivery.planned_arrival_at == original_arrival
        assert delivery.planned_departure_at == original_departure
        assert session.get(Order, DIRECT_ORDER_ID).risk_status is OrderRiskStatus.NORMAL


def test_short_delay_recalculates_picked_up_downstream_order_risk() -> None:
    with incident_client() as (client, session):
        prepare_merchant_delay(session)
        pickup = session.get(RouteStop, PICKUP_STOP_ID)
        downstream = session.get(Order, DOWNSTREAM_ORDER_ID)
        downstream_delivery = session.get(
            RouteStop, "c0000000-0000-0000-0000-000000000009"
        )
        assert pickup is not None and downstream is not None and downstream_delivery is not None
        pickup.time_window_start_at = datetime.fromisoformat("2026-09-25T10:40:00+08:00")
        pickup.planned_arrival_at = datetime.fromisoformat("2026-09-25T10:45:00+08:00")
        pickup.planned_departure_at = datetime.fromisoformat("2026-09-25T10:50:00+08:00")
        downstream.execution_status = OrderExecutionStatus.PICKED_UP
        downstream_delivery.planned_arrival_at = datetime.fromisoformat("2026-09-25T11:45:00+08:00")
        downstream_delivery.planned_departure_at = datetime.fromisoformat("2026-09-25T11:50:00+08:00")
        session.commit()

        async def scenario() -> None:
            response = await client.patch(
                f"/api/merchants/{MERCHANT_ID}/ready-time",
                json={
                    "business_date": BUSINESS_DATE,
                    "updated_ready_at": "2026-09-25T10:50:00+08:00",
                    "detected_at": "2026-09-25T09:30:00+08:00",
                },
            )
            assert response.status_code == 200, response.text

        asyncio.run(scenario())
        session.expire_all()
        assert session.get(Order, DOWNSTREAM_ORDER_ID).risk_status is OrderRiskStatus.AT_RISK
        assert session.get(RouteStop, downstream_delivery.id).planned_arrival_at == datetime.fromisoformat(
            "2026-09-25T11:50:00+08:00"
        )


def test_explicit_merchant_delay_rejects_non_positive_and_derived_fields() -> None:
    with incident_client() as (client, session):
        prepare_merchant_delay(session)

        async def scenario() -> None:
            extra_field = await client.post(
                "/api/incidents/merchant-delay",
                json={
                    "business_date": BUSINESS_DATE,
                    "merchant_id": MERCHANT_ID,
                    "updated_ready_at": "2026-09-25T10:11:00+08:00",
                    "delay_seconds": 660,
                },
            )
            assert extra_field.status_code == 422
            assert extra_field.json()["code"] == "VALIDATION_ERROR"

            non_positive = await client.post(
                "/api/incidents/merchant-delay",
                json={
                    "business_date": BUSINESS_DATE,
                    "merchant_id": MERCHANT_ID,
                    "updated_ready_at": "2026-09-25T10:00:00+08:00",
                },
            )
            assert non_positive.status_code == 422
            assert non_positive.json()["code"] == "MERCHANT_DELAY_NOT_POSITIVE"

        asyncio.run(scenario())
