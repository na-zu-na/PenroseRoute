"""Fixed normal-planning workflow with explicit transaction boundaries."""

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessError, Conflict, IntegrationError, NotFound
from app.db.models.planning import DeliveryPlanStatus, ValidationStatus
from app.db.repositories.fleet_repository import FleetRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.resource_repository import ResourceRepository
from app.integrations.optimization.contracts import SolverStatus
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.optimization.result_validator import SolverResultValidator
from app.integrations.routing.distance_matrix import build_distance_time_matrix, get_routing_provider
from app.modules.planning.finalizer import PlanFinalizer
from app.modules.planning.input_builder import (
    PlanningFacts,
    build_solver_input,
    materialize_planning_facts,
)
from app.modules.planning.queries import GeneratedPlanView, build_generated_plan_view
from app.modules.planning.validator import validate_planning_facts


_CURRENT_PLAN_UNIQUE_CONSTRAINTS = {
    "uq_delivery_plans_current_business_date",
    "uq_delivery_plans_current_group",
}


class PlanningWorkflow:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.resources = ResourceRepository(session)
        self.fleet = FleetRepository(session)
        self.plans = PlanRepository(session)

    def generate(self, business_date: date) -> GeneratedPlanView:
        return self._generate(business_date, draft=False)

    def generate_draft(self, business_date: date) -> GeneratedPlanView:
        return self._generate(business_date, draft=True)

    def _generate(self, business_date: date, *, draft: bool) -> GeneratedPlanView:
        if self.plans.get_current_plan(business_date) is not None:
            self.session.rollback()
            raise Conflict(
                code="CURRENT_PLAN_ALREADY_EXISTS",
                message="A current delivery plan already exists for this business date",
            )

        facts = self._load_facts(business_date)
        self.session.rollback()
        self._validate_facts(facts)

        try:
            provider = get_routing_provider()
            matrix = build_distance_time_matrix(facts.locations, provider=provider)
            solver_input = build_solver_input(facts, matrix)
            result = ORToolsSolver().solve(solver_input)
        except IntegrationError:
            raise
        except Exception as error:
            raise IntegrationError(
                code="SOLVER_ERROR",
                message="Planning integration failed",
            ) from error

        if result.status is SolverStatus.ERROR:
            raise IntegrationError(
                code="SOLVER_ERROR",
                message="OR-Tools failed to generate a plan",
                data={"diagnostic": result.diagnostic},
            )
        if result.status is SolverStatus.INFEASIBLE:
            raise Conflict(
                code="PLANNING_INFEASIBLE",
                message="No feasible delivery plan could be generated",
            )

        validation_issues = SolverResultValidator().validate(solver_input, result)
        if validation_issues:
            raise IntegrationError(
                code="PLAN_VALIDATION_FAILED",
                message="Solver result failed deterministic validation",
                data={
                    "issues": [
                        {"code": issue.code, "message": issue.message}
                        for issue in validation_issues
                    ]
                },
            )

        road_routes = None
        build_route = getattr(provider, "build_route", None)
        if build_route is not None:
            locations = {location.location_id: location for location in facts.locations}
            starts = {pair.vehicle_id: pair.start_location_id for pair in facts.vehicle_driver_pairs}
            road_routes = {}
            for route in result.routes:
                waypoints = (locations[starts[route.vehicle_id]], *(
                    locations[stop.location_id]
                    for stop in sorted(route.stops, key=lambda item: item.sequence_no)
                ))
                road_routes[route.vehicle_id] = build_route(waypoints)

        try:
            with self.session.begin():
                self.plans.lock_business_date_for_planning(business_date)
                if self.plans.get_current_plan(business_date) is not None:
                    raise Conflict(
                        code="CURRENT_PLAN_ALREADY_EXISTS",
                        message="A current delivery plan already exists for this business date",
                    )
                current_facts = self._load_facts(business_date)
                self._validate_facts(current_facts)
                if current_facts != facts:
                    raise BusinessError(
                        code="PLANNING_INPUT_INVALID",
                        message="Planning facts changed while the plan was being generated",
                    )
                previous = self.plans.get_latest_plan_for_business_date(business_date)
                if draft and previous is not None and previous.status is DeliveryPlanStatus.DRAFT:
                    previous.status = DeliveryPlanStatus.CANCELLED
                    self.session.flush()
                plan = PlanFinalizer(self.plans).finalize(
                    facts,
                    result,
                    activated_at=None if draft else datetime.now(timezone.utc),
                    road_routes=road_routes,
                )
                if draft:
                    plan.validation_summary = {
                        **plan.validation_summary,
                        "planning_facts_sha256": sha256(repr(facts).encode()).hexdigest(),
                    }
        except IntegrityError as error:
            constraint_name = getattr(
                getattr(error.orig, "diag", None),
                "constraint_name",
                None,
            )
            if constraint_name in _CURRENT_PLAN_UNIQUE_CONSTRAINTS:
                raise Conflict(
                    code="CURRENT_PLAN_ALREADY_EXISTS",
                    message=(
                        "A current delivery plan already exists for this "
                        "business date"
                    ),
                ) from error
            raise

        return build_generated_plan_view(plan, facts, result)

    def confirm_draft(self, plan_id: UUID):
        selected = self.plans.get_plan_by_id(plan_id)
        if selected is None:
            raise NotFound(code="DRAFT_PLAN_NOT_FOUND", message="Draft delivery plan was not found")
        business_date = selected.business_date
        self.session.rollback()
        with self.session.begin():
            self.plans.lock_business_date_for_planning(business_date)
            plan = self.plans.lock_plan_by_id(plan_id)
            latest = self.plans.get_latest_plan_for_business_date(business_date)
            if plan is None or plan.status is not DeliveryPlanStatus.DRAFT or latest is None or latest.id != plan.id:
                raise Conflict(code="DRAFT_PLAN_NOT_REVIEWABLE", message="This draft is no longer the latest reviewable plan")
            if self.plans.get_current_plan(business_date) is not None:
                raise Conflict(code="CURRENT_PLAN_ALREADY_EXISTS", message="A current delivery plan already exists for this business date")
            if plan.validation_status is not ValidationStatus.VALID:
                raise Conflict(code="DRAFT_PLAN_INVALID", message="Draft validation did not pass")
            facts = self._load_facts(business_date)
            self._validate_facts(facts)
            expected = (plan.validation_summary or {}).get("planning_facts_sha256")
            if not expected or expected != sha256(repr(facts).encode()).hexdigest():
                raise Conflict(code="PLANNING_INPUT_CHANGED", message="Planning inputs changed after draft generation; generate a new draft")
            plan.status = DeliveryPlanStatus.CURRENT
            plan.activated_at = datetime.now(timezone.utc)
            self.session.flush()
        return plan

    def _load_facts(self, business_date: date) -> PlanningFacts:
        orders = self.resources.get_orders_for_business_date(business_date)
        operational_from = min(
            (order.pickup_ready_at for order in orders),
            default=datetime.combine(business_date, time.min, timezone.utc),
        )
        operational_until = max(
            (
                order.delivery_window_end_at
                + timedelta(seconds=order.delivery_service_seconds)
                for order in orders
            ),
            default=operational_from,
        )
        assignments = self.fleet.get_vehicle_driver_pairs_for_window(
            operational_from,
            operational_until,
        )
        return materialize_planning_facts(business_date, orders, assignments)

    @staticmethod
    def _validate_facts(facts: PlanningFacts) -> None:
        issues = validate_planning_facts(facts)
        if issues:
            raise BusinessError(
                code="PLANNING_INPUT_INVALID",
                message="Planning input is incomplete or inconsistent",
                data={
                    "issues": [
                        {"code": issue.code, "message": issue.message}
                        for issue in issues
                    ]
                },
            )
        if not facts.vehicle_driver_pairs:
            raise Conflict(
                code="NO_EXECUTABLE_RESOURCE_PAIR",
                message="No executable vehicle-driver pair is available",
            )
