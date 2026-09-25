from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.resources import LocationInput, VehicleResponse
from app.schemas.recovery import RecoveryAttemptSummary


class VehicleUnavailableRequest(BaseModel):
    business_date: date
    vehicle_id: UUID
    location: LocationInput | None = None
    detected_at: AwareDatetime | None = None


class BreakdownLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    latitude: float
    longitude: float
    address_text: str | None


class VehicleUnavailableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    incident_code: str
    incident_type: str
    business_date: date
    status: str
    delivery_plan_id: UUID
    affected_vehicle_route_id: UUID
    breakdown_location: BreakdownLocationResponse
    affected_order_count: int
    handover_order_count: int
    replanning_scope: str
    recovery_required: bool


class VehicleIncidentStatusResponse(BaseModel):
    vehicle: VehicleResponse
    incident: VehicleUnavailableResponse


class MerchantDelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date
    merchant_id: UUID
    updated_ready_at: AwareDatetime
    detected_at: AwareDatetime | None = None


class MerchantDelayResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    incident_id: UUID
    incident_type: str
    business_date: date
    status: str
    original_ready_at: AwareDatetime
    updated_ready_at: AwareDatetime
    delay_seconds: int
    requires_replanning: bool
    affected_order_count: int
    replanning_scope: str | None


class IncidentListItemResponse(BaseModel):
    id: UUID
    incident_code: str
    incident_type: str
    status: str
    business_date: date
    base_delivery_plan_id: UUID
    vehicle_route_id: UUID | None
    vehicle_id: UUID | None
    merchant_id: UUID | None
    detected_at: datetime


class IncidentDetailResponse(IncidentListItemResponse):
    base_plan_code: str
    incident_location_id: UUID | None
    original_ready_at: datetime | None
    updated_ready_at: datetime | None
    delay_seconds: int | None
    resolved_at: datetime | None
    detected_by: str
    details: dict[str, Any] | None
    requires_replanning: bool
    affected_order_count: int
    handover_order_count: int
    impact_summary: dict[str, int]
    recovery_attempts: list[RecoveryAttemptSummary]


class IncidentAffectedOrderResponse(BaseModel):
    id: UUID
    incident_id: UUID
    order_id: UUID
    original_vehicle_route_id: UUID | None
    execution_status_snapshot: str
    risk_status_snapshot: str
    was_picked_up: bool
    was_completed: bool
    requires_replanning: bool
    handover_required: bool
    impact_type: str
    impact_reason: str
    assessed_at: datetime
    created_at: datetime
