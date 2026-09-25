from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.schemas.planning import RouteStopResponse


class StopActionRequest(BaseModel):
    occurred_at: AwareDatetime | None = None


class CurrentPlanOperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_plan_id: UUID
    plan_code: str
    version_no: int


class OrderOperationCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    completed: int
    in_progress: int
    at_risk: int


class ResourceOperationCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    available: int
    active: int
    unavailable: int


class MerchantOperationCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ready: int
    preparing: int
    delayed: int


class OperationsDashboardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    current_plan: CurrentPlanOperationResponse
    orders: OrderOperationCounts
    vehicles: ResourceOperationCounts
    drivers: ResourceOperationCounts
    merchants: MerchantOperationCounts
    open_incidents: int
    pending_recovery_reviews: int
    calculated_at: datetime


class OperationOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    order_code: str
    merchant_id: UUID
    merchant_name: str
    assignment_status: str
    vehicle_route_id: UUID | None
    vehicle_id: UUID | None
    driver_id: UUID | None
    execution_status: str
    risk_status: str
    pickup_eta: datetime | None
    delivery_eta: datetime | None


class OperationVehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID
    vehicle_code: str
    vehicle_status: str
    current_location_id: UUID
    driver_id: UUID
    driver_code: str
    driver_status: str
    route_id: UUID
    route_status: str


class OperationRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    route_id: UUID
    route_no: int
    vehicle_id: UUID
    driver_id: UUID
    status: str
    completed_stops: int
    total_stops: int
    current_stop_id: UUID | None
    current_stop_type: str | None
    next_stop_id: UUID | None
    next_stop_type: str | None
    next_stop_eta: datetime | None
    eta_deviation_seconds: int
    at_risk_orders: int


class StopExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stop: RouteStopResponse
    order_execution_status: str
    order_risk_status: str
    route_status: str
    vehicle_status: str
    driver_status: str
