"""The expanded seed is loadable into a fresh PostgreSQL database."""

import psycopg


def test_reference_data_splits_extra_orders_into_25th_and_27th(p1_bootstrap_database_url):
    dsn = p1_bootstrap_database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(dsn) as connection:
        def count(table):
            return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        assert count("vehicles") == 50
        assert count("drivers") == 50
        assert count("vehicle_driver_assignments") == 50
        assert count("orders") == 80
        assert connection.execute(
            "SELECT count(*) FROM orders WHERE execution_status = 'PLANNED'"
        ).fetchone()[0] == 78
        assert connection.execute(
            "SELECT count(*) FROM orders WHERE business_date = DATE '2026-09-25'"
        ).fetchone()[0] == 12
        assert connection.execute(
            "SELECT count(*) FROM orders WHERE business_date = DATE '2026-09-27'"
        ).fetchone()[0] == 68
        assert connection.execute(
            "SELECT count(*) FROM orders WHERE business_date = DATE '2026-09-27' "
            "AND order_code LIKE 'ORD-20260927-%' "
            "AND pickup_ready_at::date = DATE '2026-09-27' "
            "AND delivery_window_start_at::date = DATE '2026-09-27' "
            "AND delivery_window_end_at::date = DATE '2026-09-27'"
        ).fetchone()[0] == 68
        assert connection.execute(
            "SELECT count(*) FROM vehicle_driver_assignments WHERE status = 'PLANNED'"
        ).fetchone()[0] == 48
        assert connection.execute(
            "SELECT count(*) FROM vehicle_driver_assignments "
            "WHERE assigned_from_at::date = DATE '2026-09-27' "
            "AND assigned_until_at::date = DATE '2026-09-27'"
        ).fetchone()[0] == 47
        assert count("delivery_plans") == 3
        assert count("delivery_plan_orders") == 12
        assert count("vehicle_routes") == 5
        assert count("route_stops") == 20


def test_27th_draft_has_no_current_plan_or_generated_routes(p1_bootstrap_database_url):
    dsn = p1_bootstrap_database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(dsn) as connection:
        plan = connection.execute(
            "SELECT id, status, validation_status, vehicle_count, "
            "assigned_order_count, unassigned_order_count "
            "FROM delivery_plans WHERE business_date = DATE '2026-09-27'"
        ).fetchone()
        assert plan is not None
        assert plan[1:] == ("DRAFT", "PENDING", 0, 0, 0)
        assert connection.execute(
            "SELECT count(*) FROM delivery_plan_orders WHERE delivery_plan_id = %s",
            (plan[0],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM vehicle_routes WHERE delivery_plan_id = %s",
            (plan[0],),
        ).fetchone()[0] == 0
