"""HTTP read contracts for persisted risk alerts and their change cursor."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delivery_plan_id: UUID
    order_id: UUID
    vehicle_route_id: UUID | None
    business_date: date
    risk_type: Literal["DELIVERY_WINDOW"]
    status: Literal["ACTIVE", "RESOLVED"]
    reason_category: str | None
    evidence: dict[str, Any]
    detected_at: datetime
    last_evaluated_at: datetime
    resolved_at: datetime | None


class AlertChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    change_id: int
    alert_id: UUID
    delivery_plan_id: UUID
    order_id: UUID
    vehicle_route_id: UUID | None
    business_date: date
    risk_type: Literal["DELIVERY_WINDOW"]
    change_type: Literal["CREATED", "UPDATED", "RESOLVED"]
    reason_category: str | None
    recorded_at: datetime
    evidence_snapshot: dict[str, Any]


class AlertChangesPage(BaseModel):
    items: list[AlertChangeResponse]
    next_cursor: int
