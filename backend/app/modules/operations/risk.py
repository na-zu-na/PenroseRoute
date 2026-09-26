"""Deterministic ETA and delivery-window risk rules."""

from datetime import datetime, timedelta

from app.db.models.planning import RouteStop, StopStatus, StopType
from app.db.models.resources import OrderRiskStatus


class RiskService:
    def __init__(self, *, threshold_seconds: int) -> None:
        if threshold_seconds < 0:
            raise ValueError("threshold_seconds must be non-negative")
        self.threshold_seconds = threshold_seconds

    def status_for_eta(
        self,
        *,
        estimated_arrival_at: datetime,
        delivery_window_end_at: datetime,
    ) -> OrderRiskStatus:
        threshold_at = delivery_window_end_at - timedelta(
            seconds=self.threshold_seconds
        )
        return (
            OrderRiskStatus.AT_RISK
            if estimated_arrival_at >= threshold_at
            else OrderRiskStatus.NORMAL
        )

    def reason_for_eta(
        self, estimated_arrival_at: datetime, delivery_window_end_at: datetime
    ) -> str:
        if self.status_for_eta(
            estimated_arrival_at=estimated_arrival_at,
            delivery_window_end_at=delivery_window_end_at,
        ) is OrderRiskStatus.NORMAL:
            return "NORMAL"
        return (
            "PREDICTED_MISS"
            if estimated_arrival_at >= delivery_window_end_at
            else "APPROACHING_WINDOW"
        )

    @staticmethod
    def route_delay_seconds(
        *, stops: list[RouteStop], current_time: datetime
    ) -> int:
        next_stop = next(
            (stop for stop in stops if stop.status is not StopStatus.COMPLETED),
            None,
        )
        if next_stop is None:
            return 0
        return max(
            0,
            int((current_time - next_stop.planned_arrival_at).total_seconds()),
        )

    def recalculate_route(
        self, *, stops: list[RouteStop], current_time: datetime
    ) -> None:
        """Persist deterministic risk flags for orders on one materialized route."""
        delay_seconds = self.route_delay_seconds(
            stops=stops, current_time=current_time
        )
        for stop in stops:
            if stop.stop_type is not StopType.DELIVERY:
                continue
            if stop.status is StopStatus.COMPLETED:
                stop.order.risk_status = OrderRiskStatus.NORMAL
                continue
            window_end = stop.time_window_end_at
            if window_end is None:
                window_end = stop.order.delivery_window_end_at
            estimated_arrival = stop.planned_arrival_at + timedelta(
                seconds=delay_seconds
            )
            stop.order.risk_status = self.status_for_eta(
                estimated_arrival_at=estimated_arrival,
                delivery_window_end_at=window_end,
            )
