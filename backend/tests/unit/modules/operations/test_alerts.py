"""One ETA rule supplies both the persisted risk flag and alert reason."""

from datetime import datetime, timedelta, timezone

from app.modules.operations.risk import RiskService


END = datetime(2026, 9, 25, 11, 30, tzinfo=timezone.utc)


def test_delivery_reason_changes_only_at_deterministic_boundaries():
    risk = RiskService(threshold_seconds=900)
    assert risk.reason_for_eta(END - timedelta(seconds=901), END) == "NORMAL"
    assert risk.reason_for_eta(END - timedelta(seconds=900), END) == "APPROACHING_WINDOW"
    assert risk.reason_for_eta(END - timedelta(seconds=1), END) == "APPROACHING_WINDOW"
    assert risk.reason_for_eta(END, END) == "PREDICTED_MISS"
