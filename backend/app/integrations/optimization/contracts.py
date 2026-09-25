"""Pure Python contracts between business workflows and the optimizer.

All ``*_seconds`` values are offsets from ``SolverInput.current_time``.
Distances use meters and capacity/demand use abstract load units.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class SolverStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    ERROR = "ERROR"


class SolverStopType(StrEnum):
    PICKUP = "PICKUP"
    DELIVERY = "DELIVERY"
    HANDOVER = "HANDOVER"


class RecoveryScope(StrEnum):
    AFFECTED_ROUTE = "AFFECTED_ROUTE"
    CROSS_ROUTE = "CROSS_ROUTE"
    ALL_REMAINING = "ALL_REMAINING"


def _require_non_negative(**values: int) -> None:
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class SolverOrder:
    order_id: UUID
    pickup_location_id: UUID | None
    handover_location_id: UUID | None
    delivery_location_id: UUID
    ready_time_seconds: int
    delivery_window_start_seconds: int
    delivery_window_end_seconds: int
    pickup_service_seconds: int
    handover_service_seconds: int
    delivery_service_seconds: int
    demand_load_units: int
    required_vehicle_id: UUID | None = None

    def __post_init__(self) -> None:
        origin_count = sum(
            value is not None
            for value in (self.pickup_location_id, self.handover_location_id)
        )
        delivery_only = origin_count == 0
        if origin_count > 1 or (delivery_only and self.required_vehicle_id is None):
            raise ValueError(
                "SolverOrder requires exactly one origin, unless delivery-only work has a required vehicle"
            )
        if origin_count == 1 and self.required_vehicle_id is not None:
            raise ValueError("required_vehicle_id is only valid for delivery-only work")
        _require_non_negative(
            ready_time_seconds=self.ready_time_seconds,
            delivery_window_start_seconds=self.delivery_window_start_seconds,
            delivery_window_end_seconds=self.delivery_window_end_seconds,
            pickup_service_seconds=self.pickup_service_seconds,
            handover_service_seconds=self.handover_service_seconds,
            delivery_service_seconds=self.delivery_service_seconds,
        )
        if self.delivery_window_end_seconds < self.delivery_window_start_seconds:
            raise ValueError("delivery window end must not precede its start")
        if self.demand_load_units <= 0:
            raise ValueError("demand_load_units must be positive")
        if self.pickup_location_id is not None and self.handover_service_seconds:
            raise ValueError("pickup order cannot have handover service time")
        if self.handover_location_id is not None and self.pickup_service_seconds:
            raise ValueError("handover order cannot have pickup service time")
        if delivery_only and (
            self.pickup_service_seconds or self.handover_service_seconds
        ):
            raise ValueError("delivery-only order cannot have origin service time")


@dataclass(frozen=True, slots=True)
class SolverVehicle:
    vehicle_id: UUID
    capacity_load_units: int
    start_location_id: UUID
    available_from_seconds: int
    available_until_seconds: int
    initial_load_load_units: int = 0

    def __post_init__(self) -> None:
        _require_non_negative(
            available_from_seconds=self.available_from_seconds,
            available_until_seconds=self.available_until_seconds,
        )
        if self.capacity_load_units <= 0:
            raise ValueError("capacity_load_units must be positive")
        if not 0 <= self.initial_load_load_units <= self.capacity_load_units:
            raise ValueError("initial load must be within vehicle capacity")
        if self.available_until_seconds < self.available_from_seconds:
            raise ValueError("vehicle availability end must not precede start")


@dataclass(frozen=True, slots=True)
class SolverStop:
    order_id: UUID
    location_id: UUID
    stop_type: SolverStopType
    sequence_no: int
    arrival_time_seconds: int
    departure_time_seconds: int
    service_duration_seconds: int
    load_change_load_units: int

    def __post_init__(self) -> None:
        _require_non_negative(
            arrival_time_seconds=self.arrival_time_seconds,
            departure_time_seconds=self.departure_time_seconds,
            service_duration_seconds=self.service_duration_seconds,
        )
        if self.sequence_no <= 0:
            raise ValueError("sequence_no must be positive")
        if self.departure_time_seconds < self.arrival_time_seconds:
            raise ValueError("stop departure must not precede arrival")


@dataclass(frozen=True, slots=True)
class FrozenTask:
    vehicle_id: UUID
    stop: SolverStop


@dataclass(frozen=True, slots=True)
class SolverRoute:
    vehicle_id: UUID
    stops: tuple[SolverStop, ...]
    distance_meters: int
    duration_seconds: int

    def __post_init__(self) -> None:
        _require_non_negative(
            distance_meters=self.distance_meters,
            duration_seconds=self.duration_seconds,
        )


@dataclass(frozen=True, slots=True)
class SolverUnassignedOrder:
    order_id: UUID
    reason_code: str
    reason_detail: str | None

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("reason_code must not be empty")


@dataclass(frozen=True, slots=True)
class SolverResult:
    status: SolverStatus
    routes: tuple[SolverRoute, ...]
    unassigned_orders: tuple[SolverUnassignedOrder, ...]
    total_distance_meters: int
    total_duration_seconds: int
    diagnostic: str | None

    def __post_init__(self) -> None:
        _require_non_negative(
            total_distance_meters=self.total_distance_meters,
            total_duration_seconds=self.total_duration_seconds,
        )


@dataclass(frozen=True, slots=True)
class SolverInput:
    business_date: date
    current_time: datetime
    orders: tuple[SolverOrder, ...]
    vehicles: tuple[SolverVehicle, ...]
    frozen_tasks: tuple[FrozenTask, ...]
    recovery_scope: RecoveryScope | None
    location_ids: tuple[UUID, ...]
    distance_matrix_meters: tuple[tuple[int, ...], ...]
    duration_matrix_seconds: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        if self.current_time.tzinfo is None or self.current_time.utcoffset() is None:
            raise ValueError("current_time must be timezone-aware")
        size = len(self.location_ids)
        for name, matrix in (
            ("distance_matrix_meters", self.distance_matrix_meters),
            ("duration_matrix_seconds", self.duration_matrix_seconds),
        ):
            if len(matrix) != size or any(len(row) != size for row in matrix):
                raise ValueError(f"{name} must be square for all locations")
            if any(value < 0 for row in matrix for value in row):
                raise ValueError(f"{name} values must be non-negative")
        if len(set(self.location_ids)) != size:
            raise ValueError("location_ids must be unique")
        if len({order.order_id for order in self.orders}) != len(self.orders):
            raise ValueError("order IDs must be unique")
        if len({vehicle.vehicle_id for vehicle in self.vehicles}) != len(
            self.vehicles
        ):
            raise ValueError("vehicle IDs must be unique")
        referenced_location_ids = {
            vehicle.start_location_id for vehicle in self.vehicles
        }
        for order in self.orders:
            referenced_location_ids.add(order.delivery_location_id)
            origin_location_id = (
                order.pickup_location_id or order.handover_location_id
            )
            if origin_location_id is not None:
                referenced_location_ids.add(origin_location_id)
        referenced_location_ids.update(
            frozen.stop.location_id for frozen in self.frozen_tasks
        )
        if not referenced_location_ids.issubset(self.location_ids):
            raise ValueError("all referenced locations must exist in the matrix")
