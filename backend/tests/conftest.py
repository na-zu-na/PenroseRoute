"""Disposable PostgreSQL database for P1 migration tests."""

from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url

from app.core.config import get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def p1_database_url():
    source = make_url(get_settings().database_url)
    database_name = f"penrose_p1_test_{uuid4().hex}"
    def conninfo(name):
        return source.set(drivername="postgresql", database=name).render_as_string(hide_password=False)

    with psycopg.connect(conninfo("postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        with psycopg.connect(conninfo(database_name)) as connection:
            connection.execute((PROJECT_ROOT / "database/create_datatable.sql").read_text(encoding="utf-8"))
            connection.execute((PROJECT_ROOT / "database/reference_data.sql").read_text(encoding="utf-8"))
        yield source.set(database=database_name)
    finally:
        with psycopg.connect(conninfo("postgres"), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))
