from datetime import date
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.resources import LocationInput, VehicleResponse


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
