"""Deterministic validation for materialized normal-planning facts."""

from dataclasses import dataclass

from app.modules.planning.input_builder import PlanningFacts


@dataclass(frozen=True, slots=True)
class PlanningIssue:
    code: str
    message: str


def validate_planning_facts(facts: PlanningFacts) -> tuple[PlanningIssue, ...]:
    issues: list[PlanningIssue] = []
    if not facts.orders:
        issues.append(PlanningIssue("NO_ORDERS", "No orders exist for the business date"))

    order_ids = {order.order_id for order in facts.orders}
    if len(order_ids) != len(facts.orders):
        issues.append(PlanningIssue("DUPLICATE_ORDER", "Order IDs must be unique"))

    location_ids = {location.location_id for location in facts.locations}
    for order in facts.orders:
        if order.business_date != facts.business_date:
            issues.append(
                PlanningIssue("ORDER_DATE_MISMATCH", f"Order date differs: {order.order_code}")
            )
        if order.execution_status != "PLANNED":
            issues.append(
                PlanningIssue(
                    "ORDER_NOT_PLANNABLE",
                    f"Order has execution facts and cannot be normally planned: {order.order_code}",
                )
            )
        if order.pickup_location_id not in location_ids or order.delivery_location_id not in location_ids:
            issues.append(
                PlanningIssue("ORDER_LOCATION_MISSING", f"Order location is missing: {order.order_code}")
            )
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (
                order.pickup_ready_at,
                order.delivery_window_start_at,
                order.delivery_window_end_at,
            )
        ):
            issues.append(
                PlanningIssue("ORDER_TIMEZONE_MISSING", f"Order times require timezone: {order.order_code}")
            )

    vehicle_ids = {pair.vehicle_id for pair in facts.vehicle_driver_pairs}
    driver_ids = {pair.driver_id for pair in facts.vehicle_driver_pairs}
    if len(vehicle_ids) != len(facts.vehicle_driver_pairs) or len(driver_ids) != len(
        facts.vehicle_driver_pairs
    ):
        issues.append(
            PlanningIssue("RESOURCE_PAIR_DUPLICATED", "Vehicles and drivers must belong to one planning pair")
        )
    for pair in facts.vehicle_driver_pairs:
        if pair.vehicle_status != "AVAILABLE" or pair.driver_status != "AVAILABLE":
            issues.append(
                PlanningIssue("RESOURCE_PAIR_UNAVAILABLE", "Vehicle and driver must both be available")
            )
        if pair.start_location_id not in location_ids:
            issues.append(
                PlanningIssue("VEHICLE_LOCATION_MISSING", "Vehicle start location is missing")
            )
        if pair.assigned_until_at is not None and pair.assigned_until_at <= pair.assigned_from_at:
            issues.append(
                PlanningIssue("ASSIGNMENT_WINDOW_INVALID", "Assignment time range is invalid")
            )
    return tuple(issues)
