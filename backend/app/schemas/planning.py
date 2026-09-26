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


class OrderComparisonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    base_assignment_status: str | None
    candidate_assignment_status: str | None
    base_vehicle_id: UUID | None
    candidate_vehicle_id: UUID | None
    base_unassigned_reason_code: str | None
    candidate_unassigned_reason_code: str | None
    base_unassigned_reason_detail: str | None
    candidate_unassigned_reason_detail: str | None
    assignment_changed: bool
    route_task_changed: bool
    base_delivery_eta: datetime | None
    candidate_delivery_eta: datetime | None
    eta_delta_seconds: int | None
    eta_basis: str | None
    eta_unavailable_reason: str | None


class StopComparisonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: UUID
    stop_type: str
    source_incident_id: UUID | None
    change_type: str
    base_vehicle_id: UUID | None
    candidate_vehicle_id: UUID | None
    base_sequence_no: int | None
    candidate_sequence_no: int | None
    base_location_id: UUID | None
    candidate_location_id: UUID | None
    base_status: str | None
    candidate_status: str | None
    base_planned_arrival_at: datetime | None
    candidate_planned_arrival_at: datetime | None
    base_planned_departure_at: datetime | None
    candidate_planned_departure_at: datetime | None
    base_actual_arrival_at: datetime | None
    candidate_actual_arrival_at: datetime | None
    base_actual_departure_at: datetime | None
    candidate_actual_departure_at: datetime | None
    base_precedence_stop_type: str | None
    candidate_precedence_stop_type: str | None
    base_precedence_location_id: UUID | None
    candidate_precedence_location_id: UUID | None
    base_precedence_source_incident_id: UUID | None
    candidate_precedence_source_incident_id: UUID | None


class RouteComparisonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: UUID
    base_route_id: UUID | None
    candidate_route_id: UUID | None
    base_stop_count: int
    candidate_stop_count: int


class RemainingMetricsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    base_distance_meters: int | None
    candidate_distance_meters: int | None
    delta_distance_meters: int | None
    base_duration_seconds: int | None
    candidate_duration_seconds: int | None
    delta_duration_seconds: int | None
    reason: str | None


class PlanComparisonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recovery_plan_id: UUID
    base_plan_id: UUID
    candidate_plan_id: UUID
    business_date: date
    comparison_at: datetime
    comparison_time_basis: str
    base_plan_status: str
    candidate_plan_status: str
    reviewable: bool
    orders: tuple[OrderComparisonResponse, ...]
    stop_changes: tuple[StopComparisonResponse, ...]
    route_changes: tuple[RouteComparisonResponse, ...]
    reassigned_order_count: int
    affected_vehicle_ids: tuple[UUID, ...]
    frozen_completed_order_ids: tuple[UUID, ...]
    remaining_metrics: RemainingMetricsResponse
