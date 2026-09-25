"""Deterministic structural validation for optimizer results."""

from dataclasses import dataclass
from uuid import UUID

from app.integrations.optimization.contracts import (
    SolverInput,
    SolverResult,
    SolverStatus,
    SolverStop,
    SolverStopType,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str


class SolverResultValidator:
    def validate(
        self,
        solver_input: SolverInput,
        result: SolverResult,
    ) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []

        if result.status is not SolverStatus.FEASIBLE:
            if (
                result.routes
                or result.total_distance_meters
                or result.total_duration_seconds
            ):
                issues.append(
                    ValidationIssue(
                        code="STATUS_RESULT_CONFLICT",
                        message="Non-feasible result cannot contain route output",
                    )
                )
            return tuple(issues)

        orders = {order.order_id: order for order in solver_input.orders}
        vehicles = {
            vehicle.vehicle_id: vehicle for vehicle in solver_input.vehicles
        }
        vehicle_ids = set(vehicles)
        location_index = {
            location_id: index
            for index, location_id in enumerate(solver_input.location_ids)
        }
        route_vehicle_ids: set[UUID] = set()
        assigned_order_ids: set[UUID] = set()

        for route in result.routes:
            if route.vehicle_id not in vehicle_ids:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_VEHICLE",
                        message=f"Unknown route vehicle: {route.vehicle_id}",
                    )
                )
            if route.vehicle_id in route_vehicle_ids:
                issues.append(
                    ValidationIssue(
                        code="VEHICLE_ROUTE_DUPLICATED",
                        message=f"Vehicle has multiple routes: {route.vehicle_id}",
                    )
                )
            route_vehicle_ids.add(route.vehicle_id)

            expected_sequence = list(range(1, len(route.stops) + 1))
            if [stop.sequence_no for stop in route.stops] != expected_sequence:
                issues.append(
                    ValidationIssue(
                        code="STOP_SEQUENCE_INVALID",
                        message=f"Route stop sequence is invalid: {route.vehicle_id}",
                    )
                )

            stops_by_order: dict[UUID, list[SolverStop]] = {}
            vehicle = vehicles.get(route.vehicle_id)
            current_load = 0
            for stop in route.stops:
                if stop.order_id not in orders:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_ORDER",
                            message=f"Unknown stop order: {stop.order_id}",
                        )
                    )
                    continue
                order = orders[stop.order_id]
                expected_service = {
                    SolverStopType.PICKUP: order.pickup_service_seconds,
                    SolverStopType.HANDOVER: order.handover_service_seconds,
                    SolverStopType.DELIVERY: order.delivery_service_seconds,
                }[stop.stop_type]
                expected_load_change = (
                    -order.demand_load_units
                    if stop.stop_type is SolverStopType.DELIVERY
                    else order.demand_load_units
                )
                if (
                    stop.service_duration_seconds != expected_service
                    or stop.departure_time_seconds
                    != stop.arrival_time_seconds + expected_service
                ):
                    issues.append(
                        ValidationIssue(
                            code="STOP_SERVICE_TIME_INVALID",
                            message=f"Stop service time is invalid: {stop.order_id}",
                        )
                    )
                if stop.load_change_load_units != expected_load_change:
                    issues.append(
                        ValidationIssue(
                            code="STOP_LOAD_CHANGE_INVALID",
                            message=f"Stop load change is invalid: {stop.order_id}",
                        )
                    )
                current_load += stop.load_change_load_units
                if vehicle is not None and (
                    current_load < 0
                    or current_load > vehicle.capacity_load_units
                ):
                    issues.append(
                        ValidationIssue(
                            code="VEHICLE_CAPACITY_EXCEEDED",
                            message=f"Route load violates vehicle capacity: {route.vehicle_id}",
                        )
                    )
                if stop.stop_type in (
                    SolverStopType.PICKUP,
                    SolverStopType.HANDOVER,
                ) and stop.arrival_time_seconds < order.ready_time_seconds:
                    issues.append(
                        ValidationIssue(
                            code="PICKUP_READY_TIME_VIOLATED",
                            message=f"Order origin precedes ready time: {stop.order_id}",
                        )
                    )
                if stop.stop_type is SolverStopType.DELIVERY and not (
                    order.delivery_window_start_seconds
                    <= stop.arrival_time_seconds
                    <= order.delivery_window_end_seconds
                ):
                    issues.append(
                        ValidationIssue(
                            code="DELIVERY_WINDOW_VIOLATED",
                            message=f"Order delivery is outside its window: {stop.order_id}",
                        )
                    )
                if vehicle is not None and not (
                    vehicle.available_from_seconds
                    <= stop.arrival_time_seconds
                    and stop.departure_time_seconds
                    <= vehicle.available_until_seconds
                ):
                    issues.append(
                        ValidationIssue(
                            code="VEHICLE_AVAILABILITY_VIOLATED",
                            message=f"Route is outside vehicle availability: {route.vehicle_id}",
                        )
                    )
                stops_by_order.setdefault(stop.order_id, []).append(stop)

            if vehicle is not None and route.stops and all(
                stop.location_id in location_index for stop in route.stops
            ):
                expected_distance = 0
                previous_location_id = vehicle.start_location_id
                previous_departure = vehicle.available_from_seconds
                for stop in route.stops:
                    expected_distance += solver_input.distance_matrix_meters[
                        location_index[previous_location_id]
                    ][location_index[stop.location_id]]
                    travel_seconds = solver_input.duration_matrix_seconds[
                        location_index[previous_location_id]
                    ][location_index[stop.location_id]]
                    if stop.arrival_time_seconds < (
                        previous_departure + travel_seconds
                    ):
                        issues.append(
                            ValidationIssue(
                                code="ROUTE_TRAVEL_TIME_INVALID",
                                message=(
                                    "A route stop cannot be reached at its "
                                    f"reported time: {route.vehicle_id}"
                                ),
                            )
                        )
                    previous_location_id = stop.location_id
                    previous_departure = stop.departure_time_seconds
                if route.distance_meters != expected_distance:
                    issues.append(
                        ValidationIssue(
                            code="ROUTE_DISTANCE_MISMATCH",
                            message=f"Route distance does not match the matrix: {route.vehicle_id}",
                        )
                    )
                expected_duration = (
                    route.stops[-1].departure_time_seconds
                    - vehicle.available_from_seconds
                )
                if route.duration_seconds != expected_duration:
                    issues.append(
                        ValidationIssue(
                            code="ROUTE_DURATION_MISMATCH",
                            message=(
                                "Route duration does not match its timestamps: "
                                f"{route.vehicle_id}"
                            ),
                        )
                    )

            for order_id, stops in stops_by_order.items():
                if order_id in assigned_order_ids:
                    issues.append(
                        ValidationIssue(
                            code="ORDER_DUPLICATED",
                            message=f"Order is assigned to multiple routes: {order_id}",
                        )
                    )
                assigned_order_ids.add(order_id)
                order = orders[order_id]
                origin_type = (
                    SolverStopType.PICKUP
                    if order.pickup_location_id is not None
                    else SolverStopType.HANDOVER
                )
                origin = [stop for stop in stops if stop.stop_type is origin_type]
                delivery = [
                    stop
                    for stop in stops
                    if stop.stop_type is SolverStopType.DELIVERY
                ]
                if len(origin) != 1 or len(delivery) != 1 or len(stops) != 2:
                    issues.append(
                        ValidationIssue(
                            code="ORDER_STOP_SET_INVALID",
                            message=f"Order requires one origin and one delivery: {order_id}",
                        )
                    )
                    continue
                expected_origin_location_id = (
                    order.pickup_location_id or order.handover_location_id
                )
                if origin[0].location_id != expected_origin_location_id:
                    issues.append(
                        ValidationIssue(
                            code="ORIGIN_LOCATION_MISMATCH",
                            message=f"Order origin location is invalid: {order_id}",
                        )
                    )
                if delivery[0].location_id != order.delivery_location_id:
                    issues.append(
                        ValidationIssue(
                            code="DELIVERY_LOCATION_MISMATCH",
                            message=f"Order delivery location is invalid: {order_id}",
                        )
                    )
                if origin[0].sequence_no >= delivery[0].sequence_no:
                    issues.append(
                        ValidationIssue(
                            code="STOP_PRECEDENCE_INVALID",
                            message=f"Order delivery precedes its origin: {order_id}",
                        )
                    )

        unassigned_order_ids: set[UUID] = set()
        for unassigned in result.unassigned_orders:
            if unassigned.order_id not in orders:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_ORDER",
                        message=f"Unknown unassigned order: {unassigned.order_id}",
                    )
                )
            if (
                unassigned.order_id in unassigned_order_ids
                or unassigned.order_id in assigned_order_ids
            ):
                issues.append(
                    ValidationIssue(
                        code="ORDER_DUPLICATED",
                        message=f"Order appears more than once: {unassigned.order_id}",
                    )
                )
            unassigned_order_ids.add(unassigned.order_id)

        missing_orders = set(orders) - assigned_order_ids - unassigned_order_ids
        if missing_orders:
            issues.append(
                ValidationIssue(
                    code="ORDER_RESULT_MISSING",
                    message="Some input orders have no solver result",
                )
            )

        for frozen in solver_input.frozen_tasks:
            if not any(
                route.vehicle_id == frozen.vehicle_id
                and frozen.stop in route.stops
                for route in result.routes
            ):
                issues.append(
                    ValidationIssue(
                        code="FROZEN_TASK_MISSING",
                        message="A frozen task was not preserved on its vehicle",
                    )
                )

        if result.total_distance_meters != sum(
            route.distance_meters for route in result.routes
        ):
            issues.append(
                ValidationIssue(
                    code="TOTAL_DISTANCE_MISMATCH",
                    message="Result distance does not equal route distance total",
                )
            )
        if result.total_duration_seconds != sum(
            route.duration_seconds for route in result.routes
        ):
            issues.append(
                ValidationIssue(
                    code="TOTAL_DURATION_MISMATCH",
                    message="Result duration does not equal route duration total",
                )
            )

        return tuple(issues)
