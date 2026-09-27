"""Create or explicitly replace the isolated demo database from checked-in SQL.

The source database is only checked for connectivity. The only drop target is
the hard-coded disposable ``penrose_route_demo_rebuilt`` database.
"""

import argparse
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


STAGING_DATABASE = "penrose_route_demo_rebuilt"
SOURCE_DATABASE = "penrose_route"


def prepare(*, replace: bool) -> None:
    from app.core.config import get_settings

    source_url = make_url(get_settings().database_url)
    if source_url.database != SOURCE_DATABASE:
        raise ValueError(f"Source URL must target {SOURCE_DATABASE}")
    source_engine = create_engine(source_url, isolation_level="AUTOCOMMIT")
    stage_url = source_url.set(database=STAGING_DATABASE)
    stage_engine = create_engine(stage_url)
    try:
        with source_engine.connect() as connection:
            assert connection.scalar(text("select current_database()")) == SOURCE_DATABASE
            exists = connection.scalar(text("select 1 from pg_database where datname = :name"),
                                       {"name": STAGING_DATABASE}) is not None
            if exists and not replace:
                raise RuntimeError(f"{STAGING_DATABASE} already exists; use --replace-staging explicitly")
            if exists:
                stage_engine.dispose()
                connection.exec_driver_sql(f"DROP DATABASE {STAGING_DATABASE} WITH (FORCE)")
            connection.exec_driver_sql(f"CREATE DATABASE {STAGING_DATABASE}")
        root = Path(__file__).resolve().parents[2] / "database"
        for filename in ("create_datatable.sql", "reference_data.sql", "prepare_rebuilt_demo.sql"):
            script = (root / filename).read_text(encoding="utf-8-sig")
            # psycopg's paramstyle treats literal percent signs as placeholders.
            with stage_engine.begin() as connection:
                assert connection.scalar(text("select current_database()")) == STAGING_DATABASE
                connection.exec_driver_sql(script.replace("%", "%%"))
        print(f"Prepared {STAGING_DATABASE} from project SQL; source database unchanged")
    finally:
        stage_engine.dispose()
        source_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace-staging", action="store_true",
                        help="Delete and recreate only the dedicated staging database")
    prepare(replace=parser.parse_args().replace_staging)
