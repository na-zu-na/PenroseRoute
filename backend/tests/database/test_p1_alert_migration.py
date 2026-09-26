"""P1 upgrades run only on a disposable P0 database."""

import importlib.util
from pathlib import Path

import psycopg
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from app.db.base import Base
from app.db.models import RiskAlert, RiskAlertChange


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = PROJECT_ROOT / "database/migrations"


def _runner():
    path = PROJECT_ROOT / "database/apply_migrations.py"
    spec = importlib.util.spec_from_file_location("p1_migration_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connect(database_url):
    url = make_url(database_url)
    return psycopg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))


def test_upgrade_preserves_p0_data_and_second_run_is_noop(p1_database_url):
    runner = _runner()
    with _connect(p1_database_url) as connection:
        before = connection.execute("SELECT count(*) FROM orders").fetchone()[0]
    assert before == 6

    assert runner.apply_migrations(p1_database_url) == ["V001"]
    assert runner.apply_migrations(p1_database_url) == []

    with _connect(p1_database_url) as connection:
        assert connection.execute("SELECT count(*) FROM orders").fetchone()[0] == before
        assert connection.execute("SELECT count(*) FROM delivery_plans").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 1
        names = {row[0] for row in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )}
        assert {"risk_alerts", "risk_alert_changes", "schema_migrations"} <= names
        assert len(names) == 17


def test_alert_constraints_and_commit_safe_cursor_definition(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    with _connect(p1_database_url) as connection:
        assert connection.execute(
            "SELECT cache_size FROM pg_sequences WHERE schemaname='public' AND sequencename='risk_alert_change_cursor_seq'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_name='risk_alert_changes' AND column_name='change_id'"
        ).fetchone()[0] is None
        first = connection.execute(
            "INSERT INTO risk_alerts "
            "(delivery_plan_id, order_id, business_date, risk_type, status, evidence, detected_at, last_evaluated_at) "
            "VALUES ('80000000-0000-0000-0000-000000000001', "
            "'40000000-0000-0000-0000-000000000001', '2026-09-25', "
            "'DELIVERY_WINDOW', 'ACTIVE', '{}'::jsonb, now(), now()) RETURNING id"
        ).fetchone()[0]
        connection.commit()
        with pytest.raises(UniqueViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO risk_alerts "
                    "(delivery_plan_id, order_id, business_date, risk_type, status, evidence, detected_at, last_evaluated_at) "
                    "VALUES ('80000000-0000-0000-0000-000000000001', "
                    "'40000000-0000-0000-0000-000000000001', '2026-09-25', "
                    "'DELIVERY_WINDOW', 'ACTIVE', '{}'::jsonb, now(), now())"
                )
        with pytest.raises(CheckViolation):
            with connection.transaction():
                connection.execute(
                    "UPDATE risk_alerts SET status='RESOLVED' WHERE id=%s", (first,)
                )
        with pytest.raises(ForeignKeyViolation):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO risk_alerts "
                    "(delivery_plan_id, order_id, business_date, risk_type, status, evidence, detected_at, last_evaluated_at) "
                    "VALUES (gen_random_uuid(), gen_random_uuid(), '2026-09-25', "
                    "'DELIVERY_WINDOW', 'ACTIVE', '{}'::jsonb, now(), now())"
                )


def test_changed_applied_script_and_new_older_version_are_rejected(p1_database_url, tmp_path):
    runner = _runner()
    script = MIGRATIONS / "V001__p1_risk_alerts.sql"
    copy = tmp_path / script.name
    copy.write_bytes(script.read_bytes())
    assert runner.apply_migrations(p1_database_url, migrations_dir=tmp_path) == ["V001"]

    copy.write_bytes(copy.read_bytes() + b"\n-- altered\n")
    with pytest.raises(runner.MigrationError, match="checksum"):
        runner.apply_migrations(p1_database_url, migrations_dir=tmp_path)
    copy.write_bytes(script.read_bytes())
    (tmp_path / "V000__late_older.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(runner.MigrationError, match="out of order"):
        runner.apply_migrations(p1_database_url, migrations_dir=tmp_path)


def test_p1_orm_mapping_matches_upgraded_database(p1_database_url):
    _runner().apply_migrations(p1_database_url)
    engine = create_engine(p1_database_url)
    try:
        inspector = inspect(engine)
        for model in (RiskAlert, RiskAlertChange):
            table_name = model.__tablename__
            assert table_name in Base.metadata.tables
            actual = {column["name"]: column for column in inspector.get_columns(table_name)}
            assert set(model.__table__.columns.keys()) == set(actual)
            for column in model.__table__.columns:
                assert column.nullable == actual[column.name]["nullable"]
    finally:
        engine.dispose()


def test_runner_preserves_explicit_connection_options(p1_database_url, monkeypatch):
    runner = _runner()
    actual_connect = runner.psycopg.connect
    application_names = []

    def observed_connect(*args, **kwargs):
        connection = actual_connect(*args, **kwargs)
        application_names.append(connection.info.parameter_status("application_name"))
        return connection

    monkeypatch.setattr(runner.psycopg, "connect", observed_connect)
    url = p1_database_url.update_query_dict({"application_name": "penrose_p1_migration_test"})
    assert runner.apply_migrations(url) == ["V001"]
    assert application_names == ["penrose_p1_migration_test"]
