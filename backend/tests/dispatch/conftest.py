"""SQLite exercises real ORM queries/transactions, not PostgreSQL DDL/trigger parity."""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import Column, JSON, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Location, Merchant, Customer, Order, Vehicle, Driver, VehicleDriverAssignment


@pytest.fixture
def database():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        Table(table.name, metadata, *[
            Column(c.name, JSON() if isinstance(c.type, JSONB) else c.type,
                   primary_key=c.primary_key, nullable=True) for c in table.columns])
    metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 25, 0, tzinfo=timezone.utc)
    day = date(2026, 9, 25)
    ids = {k: uuid4() for k in ["pickup", "delivery", "merchant", "customer", "order", "vehicle", "driver", "assignment"]}
    with sessions() as s, s.begin():
        s.add_all([Location(id=ids["pickup"], display_name="Pickup", latitude=1.30, longitude=103.80),
                   Location(id=ids["delivery"], display_name="Delivery", latitude=1.31, longitude=103.81)])
        s.add(Merchant(id=ids["merchant"], merchant_code="M1", name="Merchant", pickup_location_id=ids["pickup"],
                       preparation_status="READY", default_pickup_service_seconds=0))
        s.add(Customer(id=ids["customer"], customer_code="C1", name="Customer", default_delivery_location_id=ids["delivery"]))
        s.add(Order(id=ids["order"], order_code="O1", business_date=day, merchant_id=ids["merchant"], customer_id=ids["customer"],
            pickup_location_id=ids["pickup"], delivery_location_id=ids["delivery"], pickup_ready_at=now,
            pickup_service_seconds=30, delivery_service_seconds=30, delivery_window_start_at=now,
            delivery_window_end_at=now.replace(hour=8), demand_load_units=2, execution_status="PLANNED", risk_status="NORMAL"))
        s.add(Vehicle(id=ids["vehicle"], vehicle_code="V1", name="Vehicle", capacity_load_units=4,
                      status="AVAILABLE", current_location_id=ids["pickup"], current_location_recorded_at=now))
        s.add(Driver(id=ids["driver"], driver_code="D1", name="Driver", status="AVAILABLE"))
        s.add(VehicleDriverAssignment(id=ids["assignment"], vehicle_id=ids["vehicle"], driver_id=ids["driver"],
            status="ACTIVE", assigned_from_at=now, assigned_until_at=now.replace(hour=12), activated_at=now))
    yield sessions, now, day, ids
    engine.dispose()
