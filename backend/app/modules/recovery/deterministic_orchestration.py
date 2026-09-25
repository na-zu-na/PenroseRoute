"""Deterministic recovery orchestration; no LLM or agent integration."""

from dataclasses import dataclass

from app.integrations.optimization.contracts import (
    RecoveryScope,
    SolverInput,
    SolverOrder,
    SolverResult,
    SolverStatus,
    SolverVehicle,
)
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.optimization.result_validator import (
    SolverResultValidator,
    ValidationIssue,
)
from app.integrations.routing.distance_matrix import build_distance_time_matrix
from app.modules.recovery.deterministic_context import RecoveryContext


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    solver_input: SolverInput
    solver_result: SolverResult
    validation_issues: tuple[ValidationIssue, ...]


def execute_recovery(context: RecoveryContext) -> OrchestrationResult:
    matrix = build_distance_time_matrix(context.locations)
    solver_input = build_recovery_solver_input(context, matrix)
    missing_vehicle_ids = {
        order.required_vehicle_id
        for order in solver_input.orders
        if order.required_vehicle_id is not None
    } - {vehicle.vehicle_id for vehicle in solver_input.vehicles}
    if missing_vehicle_ids:
        result = SolverResult(
            status=SolverStatus.INFEASIBLE,
            routes=(),
            unassigned_orders=(),
            total_distance_meters=0,
            total_duration_seconds=0,
            diagnostic="Onboard orders have no available vehicle-driver pair",
        )
        return OrchestrationResult(solver_input, result, ())
    result = ORToolsSolver().solve(solver_input)
    result = _require_all_recovery_orders_assigned(solver_input, result)
    issues = SolverResultValidator().validate(solver_input, result)
    return OrchestrationResult(
        solver_input=solver_input,
        solver_result=result,
        validation_issues=issues,
    )


def build_recovery_solver_input(context: RecoveryContext, matrix) -> SolverInput:
    def seconds(value) -> int:
        return max(0, int((value - context.current_time).total_seconds()))

    return SolverInput(
        business_date=context.business_date,
        current_time=context.current_time,
        orders=tuple(
            SolverOrder(
                order_id=order.order_id,
                pickup_location_id=(
                    None
                    if order.handover_required or order.delivery_only
                    else order.pickup_location_id
                ),
                handover_location_id=(
                    order.handover_location_id if order.handover_required else None
                ),
                delivery_location_id=order.delivery_location_id,
                ready_time_seconds=(
                    0
                    if order.handover_required
                    else seconds(order.pickup_ready_at)
                ),
                delivery_window_start_seconds=seconds(
                    order.delivery_window_start_at
                ),
                delivery_window_end_seconds=seconds(order.delivery_window_end_at),
                pickup_service_seconds=(
                    0
                    if order.handover_required or order.delivery_only
                    else order.pickup_service_seconds
                ),
                handover_service_seconds=(
                    order.pickup_service_seconds if order.handover_required else 0
                ),
                delivery_service_seconds=order.delivery_service_seconds,
                demand_load_units=order.demand_load_units,
                required_vehicle_id=order.required_vehicle_id,
            )
            for order in context.target_orders
        ),
        vehicles=tuple(
            SolverVehicle(
                vehicle_id=vehicle.vehicle_id,
                capacity_load_units=vehicle.capacity_load_units,
                initial_load_load_units=vehicle.initial_load_load_units,
                start_location_id=vehicle.start_location_id,
                available_from_seconds=seconds(vehicle.available_from),
                available_until_seconds=seconds(vehicle.available_until),
            )
            for vehicle in context.vehicles
        ),
        frozen_tasks=(),
        recovery_scope=RecoveryScope(context.scope.value),
        location_ids=matrix.location_ids,
        distance_matrix_meters=matrix.distance_matrix_meters,
        duration_matrix_seconds=matrix.duration_matrix_seconds,
    )


def _require_all_recovery_orders_assigned(
    solver_input: SolverInput,
    result: SolverResult,
) -> SolverResult:
    if result.status is not SolverStatus.FEASIBLE:
        return result
    if not result.unassigned_orders:
        return result
    return SolverResult(
        status=SolverStatus.INFEASIBLE,
        routes=(),
        unassigned_orders=(),
        total_distance_meters=0,
        total_duration_seconds=0,
        diagnostic=(
            "Recovery requires every scoped order to be assigned; "
            f"{len(result.unassigned_orders)} remained unassigned"
        ),
    )
