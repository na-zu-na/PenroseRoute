import warnings

from sqlalchemy import inspect, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import configure_mappers

from app.db.base import Base
from app.db.models import DeliveryPlan, Merchant, Order, Vehicle
from app.db.session import SessionLocal, engine


EXPECTED_TABLES = {
    "customers",
    "delivery_plan_orders",
    "delivery_plans",
    "drivers",
    "incident_affected_orders",
    "incidents",
    "locations",
    "merchants",
    "orders",
    "recovery_plans",
    "route_stops",
    "vehicle_driver_assignments",
    "vehicle_routes",
    "vehicles",
    "risk_alerts",
    "risk_alert_changes",
}


def test_all_relationship_mappers_configure_without_warnings() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        configure_mappers()


def test_metadata_matches_existing_database_tables_columns_and_foreign_keys() -> None:
    inspector = inspect(engine)

    # The migration ledger is database infrastructure, not an ORM model.
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert set(inspector.get_table_names(schema="public")) == EXPECTED_TABLES | {"schema_migrations"}

    for table_name in EXPECTED_TABLES:
        mapped_table = Base.metadata.tables[table_name]
        database_columns = {
            column["name"]: column for column in inspector.get_columns(table_name)
        }

        assert set(mapped_table.columns.keys()) == set(database_columns)
        for column in mapped_table.columns:
            assert column.nullable is database_columns[column.name]["nullable"]

        mapped_foreign_keys = {
            (
                tuple(constraint.column_keys),
                tuple(element.target_fullname for element in constraint.elements),
            )
            for constraint in mapped_table.foreign_key_constraints
        }
        database_foreign_keys = {
            (
                tuple(constraint["constrained_columns"]),
                tuple(
                    f"{constraint['referred_table']}.{column}"
                    for column in constraint["referred_columns"]
                ),
            )
            for constraint in inspector.get_foreign_keys(table_name)
        }
        assert mapped_foreign_keys == database_foreign_keys

        mapped_check_names = {
            constraint.name
            for constraint in mapped_table.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        database_check_names = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(table_name)
        }
        assert mapped_check_names == database_check_names


def test_sync_session_reads_existing_seed_data() -> None:
    with SessionLocal() as session:
        merchant = session.scalar(
            select(Merchant).where(Merchant.merchant_code == "MER-001")
        )
        order = session.scalar(
            select(Order).where(Order.order_code == "ORD-20260925-001")
        )
        vehicle = session.scalar(
            select(Vehicle).where(Vehicle.vehicle_code == "VEH-001")
        )
        delivery_plan = session.scalar(
            select(DeliveryPlan).where(DeliveryPlan.plan_code == "PLAN-20260925-V1")
        )

        assert merchant is not None
        assert merchant.pickup_location.display_name == "Jurong Merchant Pickup"
        assert order is not None
        assert order.merchant_id == merchant.id
        assert vehicle is not None
        assert vehicle.current_location is not None
        assert delivery_plan is not None
        assert len(delivery_plan.routes) == 2
