from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GeneratePlanRequest(BaseModel):
    business_date: date


class PlanSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_orders: int
    assigned_orders: int
    unassigned_orders: int
    vehicle_count: int
    total_distance_meters: int
    total_duration_seconds: int


class UnassignedOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    order_code: str
    unassigned_reason_code: str
    unassigned_reason_detail: str | None


class GeneratePlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_plan_id: UUID
    plan_code: str
    business_date: date
    version_no: int
    status: str
    summary: PlanSummaryResponse
    unassigned_orders: tuple[UnassignedOrderResponse, ...]


class DeliveryPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_code: str
    plan_group_id: UUID
    business_date: date
    version_no: int
    parent_plan_id: UUID | None
    status: str
    solver_engine: str
    validation_status: str
    total_distance_meters: int | None
    total_duration_seconds: int | None
    vehicle_count: int
    assigned_order_count: int
    unassigned_order_count: int
    validation_summary: dict[str, Any] | None
    activated_at: datetime | None
    superseded_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class PlanOrderMembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    order_code: str
    assignment_status: str
    vehicle_route_id: UUID | None
    unassigned_reason_code: str | None
    unassigned_reason_detail: str | None


class VehicleRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    delivery_plan_id: UUID
    route_no: int
    vehicle_id: UUID
    driver_id: UUID
    vehicle_driver_assignment_id: UUID
    start_location_id: UUID
    end_location_id: UUID
    status: str
    planned_start_at: datetime
    planned_end_at: datetime
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    distance_meters: int
    duration_seconds: int
    vehicle_capacity_load_units_snapshot: int
    route_geometry: dict[str, Any] | None
    route_metrics: dict[str, Any] | None


class RouteStopResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    vehicle_route_id: UUID
    order_id: UUID
    location_id: UUID
    stop_type: str
    sequence_no: int
    precedence_stop_id: UUID | None
    source_incident_id: UUID | None
    planned_arrival_at: datetime
    planned_departure_at: datetime
    actual_arrival_at: datetime | None
    actual_departure_at: datetime | None
    service_seconds: int
    time_window_start_at: datetime | None
    time_window_end_at: datetime | None
    demand_load_units_snapshot: int
    status: str
