"""The deployment seed must load into a fresh PostgreSQL database."""

from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.planning.workflow import PlanningWorkflow


DATABASE_DIR = Path(__file__).resolve().parents[3] / "database"


def test_future_week_seed_loads_seven_plannable_days(empty_database_url):
    conninfo = empty_database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(conninfo, autocommit=True) as connection:
        for filename in (
            "create_datatable.sql",
            "reference_data_future_week.sql",
            "validate_future_week_seed.sql",
        ):
            connection.execute((DATABASE_DIR / filename).read_text(encoding="utf-8"))
        business_dates = [
            row[0] for row in connection.execute(
                "SELECT DISTINCT business_date FROM public.orders ORDER BY business_date"
            )
        ]

    engine = create_engine(empty_database_url)
    try:
        with Session(engine) as session:
            for business_date in business_dates:
                plan = PlanningWorkflow(session).generate(business_date)
                assert plan.business_date == business_date
                assert plan.summary.assigned_orders == 12
    finally:
        engine.dispose()

    with psycopg.connect(conninfo, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="requires an empty database"):
            connection.execute(
                (DATABASE_DIR / "reference_data_future_week.sql").read_text(encoding="utf-8")
            )
