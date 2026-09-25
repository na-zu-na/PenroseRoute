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
        vehicle_ids = {vehicle.vehicle_id for vehicle in solver_input.vehicles}
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
            for stop in route.stops:
                if stop.order_id not in orders:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_ORDER",
                            message=f"Unknown stop order: {stop.order_id}",
                        )
                    )
                    continue
                stops_by_order.setdefault(stop.order_id, []).append(stop)

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
