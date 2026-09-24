from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = PROJECT_ROOT / "database" / "create_database.sql"


def test_database_bootstrap_creates_only_the_database_without_credentials() -> None:
    sql = BOOTSTRAP.read_text(encoding="utf-8")

    assert "CREATE DATABASE" in sql
    assert "CREATE TABLE" not in sql
    assert "CREATE ROLE" not in sql
    assert "PASSWORD" not in sql.upper()
