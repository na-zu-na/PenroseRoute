from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.fleet import Vehicle
    from app.db.models.planning import DeliveryPlanOrder, RouteStop
    from app.db.models.recovery import Incident, IncidentAffectedOrder


def _enum(enum: type[StrEnum], *, length: int, name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        length=length,
    )


class MerchantPreparationStatus(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"
    DELAYED = "DELAYED"


class OrderExecutionStatus(StrEnum):
    PLANNED = "PLANNED"
    PICKUP_IN_PROGRESS = "PICKUP_IN_PROGRESS"
    PICKED_UP = "PICKED_UP"
    DELIVERING = "DELIVERING"
    COMPLETED = "COMPLETED"


class OrderRiskStatus(StrEnum):
    NORMAL = "NORMAL"
    AT_RISK = "AT_RISK"


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("btrim(display_name) <> ''", name="ck_locations_display_name"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_locations_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_locations_longitude"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    location_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    address_text: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    merchants: Mapped[list[Merchant]] = relationship(back_populates="pickup_location")
    customers: Mapped[list[Customer]] = relationship(back_populates="default_delivery_location")
    pickup_orders: Mapped[list[Order]] = relationship(back_populates="pickup_location", foreign_keys="Order.pickup_location_id")
    delivery_orders: Mapped[list[Order]] = relationship(back_populates="delivery_location", foreign_keys="Order.delivery_location_id")
    vehicles: Mapped[list[Vehicle]] = relationship(back_populates="current_location")
    route_stops: Mapped[list[RouteStop]] = relationship(back_populates="location")
    incidents: Mapped[list[Incident]] = relationship(back_populates="incident_location")


class Merchant(Base):
    __tablename__ = "merchants"
    __table_args__ = (
        CheckConstraint("btrim(merchant_code) <> ''", name="ck_merchants_code"),
        CheckConstraint("btrim(name) <> ''", name="ck_merchants_name"),
        CheckConstraint("default_pickup_service_seconds >= 0", name="ck_merchants_pickup_service"),
        CheckConstraint("preparation_status IN ('PREPARING','READY','DELAYED')", name="ck_merchants_preparation_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    merchant_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    pickup_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    preparation_status: Mapped[MerchantPreparationStatus] = mapped_column(
        _enum(MerchantPreparationStatus, length=16, name="merchant_preparation_status"),
        server_default=text("'PREPARING'"),
    )
    operational_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    default_pickup_service_seconds: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    pickup_location: Mapped[Location] = relationship(back_populates="merchants")
    orders: Mapped[list[Order]] = relationship(back_populates="merchant")
    incidents: Mapped[list[Incident]] = relationship(back_populates="merchant")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("btrim(customer_code) <> ''", name="ck_customers_code"),
        CheckConstraint("btrim(name) <> ''", name="ck_customers_name"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    customer_code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    default_delivery_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    default_delivery_location: Mapped[Location] = relationship(back_populates="customers")
    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("btrim(order_code) <> ''", name="ck_orders_code"),
        CheckConstraint("delivery_window_end_at >= delivery_window_start_at", name="ck_orders_delivery_window"),
        CheckConstraint("pickup_service_seconds >= 0", name="ck_orders_pickup_service"),
        CheckConstraint("delivery_service_seconds >= 0", name="ck_orders_delivery_service"),
        CheckConstraint("demand_load_units > 0", name="ck_orders_demand"),
        CheckConstraint(
            "execution_status IN ('PLANNED','PICKUP_IN_PROGRESS','PICKED_UP','DELIVERING','COMPLETED')",
            name="ck_orders_execution_status",
        ),
        CheckConstraint("risk_status IN ('NORMAL','AT_RISK')", name="ck_orders_risk_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    order_code: Mapped[str] = mapped_column(String(64), unique=True)
    business_date: Mapped[date] = mapped_column(Date)
    merchant_id: Mapped[UUID] = mapped_column(ForeignKey("merchants.id", ondelete="RESTRICT"))
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"))
    pickup_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    delivery_location_id: Mapped[UUID] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"))
    pickup_ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pickup_service_seconds: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    delivery_window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_service_seconds: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    demand_load_units: Mapped[int] = mapped_column(Integer)
    execution_status: Mapped[OrderExecutionStatus] = mapped_column(
        _enum(OrderExecutionStatus, length=32, name="order_execution_status"),
        server_default=text("'PLANNED'"),
    )
    risk_status: Mapped[OrderRiskStatus] = mapped_column(
        _enum(OrderRiskStatus, length=16, name="order_risk_status"),
        server_default=text("'NORMAL'"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))

    merchant: Mapped[Merchant] = relationship(back_populates="orders")
    customer: Mapped[Customer] = relationship(back_populates="orders")
    pickup_location: Mapped[Location] = relationship(back_populates="pickup_orders", foreign_keys=[pickup_location_id])
    delivery_location: Mapped[Location] = relationship(back_populates="delivery_orders", foreign_keys=[delivery_location_id])
    plan_orders: Mapped[list[DeliveryPlanOrder]] = relationship(back_populates="order")
    route_stops: Mapped[list[RouteStop]] = relationship(back_populates="order")
    incident_impacts: Mapped[list[IncidentAffectedOrder]] = relationship(back_populates="order")
