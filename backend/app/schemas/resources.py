from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints


Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class MerchantStatus(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"
    DELAYED = "DELAYED"


class MerchantWritableStatus(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"


class ResourceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    ACTIVE = "ACTIVE"
    UNAVAILABLE = "UNAVAILABLE"


class LocationInput(BaseModel):
    location_code: Code
    address_text: str | None = None
    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)


class LocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    location_code: str | None
    display_name: str
    address_text: str | None
    latitude: float
    longitude: float


class MerchantCreate(BaseModel):
    merchant_code: Code
    name: Name
    pickup_location: LocationInput
    operational_ready_at: AwareDatetime | None = None
    default_pickup_service_seconds: int = Field(default=0, ge=0)


class MerchantReplace(BaseModel):
    name: Name
    pickup_location: LocationInput
    default_pickup_service_seconds: int = Field(ge=0)


class MerchantStatusUpdate(BaseModel):
    status: MerchantWritableStatus


class MerchantReadyTimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date
    updated_ready_at: AwareDatetime
    detected_at: AwareDatetime | None = None


class MerchantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    merchant_code: str
    name: str
    pickup_location: LocationResponse
    preparation_status: MerchantStatus
    operational_ready_at: datetime | None
    default_pickup_service_seconds: int
    created_at: datetime
    updated_at: datetime


class CustomerCreate(BaseModel):
    customer_code: Code
    name: Name
    default_delivery_location: LocationInput


class CustomerReplace(BaseModel):
    name: Name
    default_delivery_location: LocationInput


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_code: str
    name: str
    default_delivery_location: LocationResponse
    created_at: datetime
    updated_at: datetime


class VehicleCreate(BaseModel):
    vehicle_code: Code
    name: Name
    capacity_load_units: int = Field(gt=0)
    current_location: LocationInput
    current_location_recorded_at: AwareDatetime


class VehicleReplace(BaseModel):
    name: Name
    capacity_load_units: int = Field(gt=0)


class VehicleStatusUpdate(BaseModel):
    status: ResourceStatus
    business_date: date | None = None


class VehicleLocationUpdate(BaseModel):
    location: LocationInput
    recorded_at: AwareDatetime


class VehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    vehicle_code: str
    name: str
    capacity_load_units: int
    status: ResourceStatus
    current_location: LocationResponse
    current_location_recorded_at: datetime
    created_at: datetime
    updated_at: datetime


class DriverCreate(BaseModel):
    driver_code: Code
    name: Name


class DriverReplace(BaseModel):
    name: Name


class DriverStatusUpdate(BaseModel):
    status: ResourceStatus


class DriverResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    driver_code: str
    name: str
    status: ResourceStatus
    created_at: datetime
    updated_at: datetime


class DriverStatusResult(BaseModel):
    driver: DriverResponse
    manual_intervention_required: bool
