from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_FILE = PROJECT_ROOT / "database" / "create_database.sql"
EXPECTED_TABLES = {
    "locations",
    "merchants",
    "customers",
    "orders",
    "vehicles",
    "drivers",
    "vehicle_driver_assignments",
    "delivery_plans",
    "vehicle_routes",
    "incidents",
    "delivery_plan_orders",
    "route_stops",
    "incident_affected_orders",
    "recovery_plans",
}


def test_single_sql_script_creates_database_and_complete_p0_schema() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    created_tables = set(re.findall(r"CREATE TABLE ([a-z_]+)", sql))

    assert "CREATE DATABASE" in sql
    assert "\\connect :database_name" in sql
    assert re.search(r"\\connect :database_name\s+BEGIN;", sql)
    assert sql.rstrip().endswith("COMMIT;")
    assert created_tables == EXPECTED_TABLES
    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in sql
    assert "CREATE EXTENSION IF NOT EXISTS btree_gist" in sql


def test_sql_schema_keeps_the_critical_p0_guards() -> None:
    sql = SCHEMA_FILE.read_text(encoding="utf-8")

    for fragment in (
        "EXCLUDE USING gist",
        "uq_delivery_plans_current_business_date",
        "uq_incidents_open_vehicle",
        "fk_delivery_plan_orders_route",
        "fk_vehicle_routes_assignment",
        "ck_recovery_plans_solver_outcome",
        "prevent_completed_order_regression",
        "prevent_completed_stop_regression",
        "validate_route_stop_relationships",
        "DEFERRABLE INITIALLY DEFERRED",
    ):
        assert fragment in sql
