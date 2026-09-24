from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = PROJECT_ROOT / "database" / "create_database.sql"


def test_database_bootstrap_is_idempotent_and_contains_no_credentials() -> None:
    sql = BOOTSTRAP.read_text(encoding="utf-8")

    assert "\\set ON_ERROR_STOP on" in sql
    assert "FROM pg_database" in sql
    assert "CREATE DATABASE" in sql
    assert "\\gexec" in sql
    assert "CREATE ROLE" not in sql
    assert "PASSWORD" not in sql.upper()
