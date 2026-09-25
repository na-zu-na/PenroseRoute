from datetime import datetime, timedelta, timezone

from app.db.models.resources import OrderRiskStatus
from app.modules.operations.risk import RiskService


def test_risk_service_marks_eta_inside_threshold_at_risk() -> None:
    window_end = datetime(2026, 10, 3, 10, 30, tzinfo=timezone.utc)
    service = RiskService(threshold_seconds=900)

    assert service.status_for_eta(
        estimated_arrival_at=window_end - timedelta(seconds=900),
        delivery_window_end_at=window_end,
    ) is OrderRiskStatus.AT_RISK


def test_risk_service_keeps_earlier_eta_normal() -> None:
    window_end = datetime(2026, 10, 3, 10, 30, tzinfo=timezone.utc)
    service = RiskService(threshold_seconds=900)

    assert service.status_for_eta(
        estimated_arrival_at=window_end - timedelta(seconds=901),
        delivery_window_end_at=window_end,
    ) is OrderRiskStatus.NORMAL
