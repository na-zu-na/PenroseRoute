import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_database_url_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:pass@db:5432/test",
    )

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+psycopg://user:pass@db:5432/test"


def test_alert_scan_interval_defaults_to_thirty_seconds() -> None:
    assert Settings(_env_file=None).alert_scan_interval_seconds == 30


def test_alert_scan_interval_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alert_scan_interval_seconds=0)
