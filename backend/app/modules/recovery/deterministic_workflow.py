"""Recovery attempt lifecycle and candidate-plan persistence."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.errors import BusinessError, Conflict, IntegrationError, NotFound
from app.db.models import (
    DeliveryPlan,
    DeliveryPlanOrder,
    RecoveryPlan,
    RouteStop,
    VehicleRoute,
)
from app.db.models.planning import (
    DeliveryPlanStatus,
    PlanOrderAssignmentStatus,
    RouteStatus,
    StopStatus,
    StopType,
    ValidationStatus,
)
from app.db.models.recovery import (
    IncidentStatus,
    RecoveryPlanStatus,
    ReplanningScope,
    SolverStatus as RecoverySolverStatus,
)
from app.db.repositories.incident_repository import IncidentRepository
from app.db.repositories.plan_repository import PlanRepository
from app.db.repositories.recovery_repository import RecoveryRepository
from app.integrations.optimization.contracts import (
    SolverResult,
    SolverStatus,
    SolverStopType,
)
from app.modules.incidents.scope import (
    initial_merchant_delay_scope,
    initial_vehicle_unavailable_scope,
    next_replanning_scope,
)
from app.modules.recovery.deterministic_context import (
    RecoveryContext,
    RouteSnapshot,
    StopSnapshot,
    materialize_recovery_context,
)
from app.modules.recovery.deterministic_orchestration import execute_recovery


@dataclass(frozen=True, slots=True)
class RecoveryAttemptView:
    recovery_plan_id: UUID
    attempt_no: int
    replanning_scope: str
    status: str
    solver_status: str | None
    validation_status: str | None
    candidate_delivery_plan_id: UUID | None


@dataclass(frozen=True, slots=True)
class StartRecoveryResult:
    incident_id: UUID
    outcome: str
    attempts_created: tuple[RecoveryAttemptView, ...]
    reviewable_recovery_plan_id: UUID | None
    candidate_delivery_plan_id: UUID | None
    agent_explanation: str | None
    manual_intervention_required: bool


class RecoveryWorkflow:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.incidents = IncidentRepository(session)
        self.plans = PlanRepository(session)
        self.recoveries = RecoveryRepository(session)

    def start(self, incident_id: UUID) -> StartRecoveryResult:
        with self.session.begin():
            incident = self.incidents.lock_incident_by_id(incident_id)
            if incident is None:
                raise NotFound(
                    code="INCIDENT_NOT_FOUND", message="Incident was not found"
                )
            existing = self.recoveries.get_recovery_attempts(incident_id)
            if any(
                item.status is RecoveryPlanStatus.PENDING_REVIEW
                for item in existing
            ):
                raise Conflict(
                    code="RECOVERY_ALREADY_PENDING_REVIEW",
                    message="A recovery candidate is already pending review",
                )
            if existing:
                raise Conflict(
                    code="RECOVERY_ALREADY_IN_PROGRESS",
                    message="Recovery attempts already exist for this incident",
                )
            scope = self._initial_scope(incident.incident_type.value)
            context = materialize_recovery_context(
                self.session, incident_id=incident_id, scope=scope
            )
            attempt = self._new_attempt(context, attempt_no=1, previous_id=None)

        return self._execute_attempts(incident_id, context, attempt)

    def resume(self, recovery_plan_id: UUID) -> StartRecoveryResult:
        """Run an already committed DRAFT attempt after a dispatcher Modify."""
        with self.session.begin():
            attempt = self._lock_attempt(recovery_plan_id)
            if (
                attempt.status is not RecoveryPlanStatus.DRAFT
                or attempt.solver_status is not None
                or attempt.candidate_delivery_plan_id is not None
            ):
                raise Conflict(
                    code="RECOVERY_ALREADY_IN_PROGRESS",
                    message="Recovery attempt has already been processed",
                )
            self._assert_base_still_current(
                attempt.incident_id, attempt.base_delivery_plan_id
            )
            context = materialize_recovery_context(
                self.session,
                incident_id=attempt.incident_id,
                scope=attempt.replanning_scope,
            )
            if context.base_plan_id != attempt.base_delivery_plan_id:
                raise Conflict(
                    code="BASE_PLAN_NOT_CURRENT",
                    message="Recovery attempt base plan changed",
                )
            incident_id = attempt.incident_id

        return self._execute_attempts(incident_id, context, attempt)

    def _execute_attempts(
        self, incident_id: UUID, context: RecoveryContext, attempt: RecoveryPlan
    ) -> StartRecoveryResult:
        created: list[RecoveryAttemptView] = []
        while True:
            try:
                outcome = execute_recovery(context)
            except Exception as error:
                with self.session.begin():
                    locked = self._lock_attempt(attempt.id)
                    locked.solver_status = RecoverySolverStatus.ERROR
                    locked.solver_validation_summary = {
                        "diagnostic": str(error),
                        "failure_stage": "recovery_orchestration",
                    }
                    self.recoveries.flush()
                    failed = self._view(locked)
                raise IntegrationError(
                    code="RECOVERY_EXECUTION_ERROR",
                    message=(
                        "Recovery orchestration failed; automatic scope "
                        "expansion stopped"
                    ),
                    data={
                        **self._view_data(failed),
                        "manual_intervention_required": True,
                    },
                ) from error
            result = outcome.solver_result
            if result.status is SolverStatus.INFEASIBLE:
                with self.session.begin():
                    locked = self._lock_attempt(attempt.id)
                    locked.solver_status = RecoverySolverStatus.INFEASIBLE
                    locked.solver_validation_summary = {
                        "diagnostic": result.diagnostic,
                    }
                    self.recoveries.flush()
                    created.append(self._view(locked))
                next_scope = next_replanning_scope(context.scope)
                if next_scope is None:
                    return StartRecoveryResult(
                        incident_id=incident_id,
                        outcome="NO_FEASIBLE_RECOVERY",
                        attempts_created=tuple(created),
                        reviewable_recovery_plan_id=None,
                        candidate_delivery_plan_id=None,
                        agent_explanation=None,
                        manual_intervention_required=True,
                    )
                with self.session.begin():
                    self._assert_base_still_current(incident_id, context.base_plan_id)
                    context = materialize_recovery_context(
                        self.session,
                        incident_id=incident_id,
                        scope=next_scope,
                        operational_time=context.current_time,
                    )
                    attempt = self._new_attempt(
                        context,
                        attempt_no=attempt.attempt_no + 1,
                        previous_id=attempt.id,
                    )
                continue

            if result.status is SolverStatus.ERROR:
                with self.session.begin():
                    locked = self._lock_attempt(attempt.id)
                    locked.solver_status = RecoverySolverStatus.ERROR
                    locked.solver_validation_summary = {
                        "diagnostic": result.diagnostic,
                    }
                    self.recoveries.flush()
                    failed = self._view(locked)
                raise IntegrationError(
                    code="RECOVERY_SOLVER_ERROR",
                    message=(
                        "Recovery solver execution failed; automatic scope "
                        "expansion stopped"
                    ),
                    data={**self._view_data(failed), "manual_intervention_required": True},
                )

            if outcome.validation_issues:
                with self.session.begin():
                    locked = self._lock_attempt(attempt.id)
                    locked.solver_status = RecoverySolverStatus.FEASIBLE
                    locked.validation_status = ValidationStatus.INVALID
                    locked.solver_validation_summary = {
                        "issues": [
                            {"code": item.code, "message": item.message}
                            for item in outcome.validation_issues
                        ]
                    }
                    self.recoveries.flush()
                    failed = self._view(locked)
                raise IntegrationError(
                    code="RECOVERY_VALIDATION_FAILED",
                    message=(
                        "Recovery result validation failed; automatic scope "
                        "expansion stopped"
                    ),
                    data={**self._view_data(failed), "manual_intervention_required": True},
                )

            explanation = self._deterministic_summary(context, len(created))
            with self.session.begin():
                self._assert_base_still_current(incident_id, context.base_plan_id)
                self.plans.lock_recovery_execution_facts(
                    context.base_plan_id,
                    extra_vehicle_ids=tuple(
                        item.vehicle_id for item in context.vehicles
                    ),
                    extra_driver_ids=tuple(
                        item.driver_id for item in context.vehicles
                    ),
                    extra_assignment_ids=tuple(
                        item.assignment_id for item in context.vehicles
                    ),
                )
                self.session.expire_all()
                current_context = materialize_recovery_context(
                    self.session,
                    incident_id=incident_id,
                    scope=context.scope,
                    operational_time=context.current_time,
                )
                if current_context != context:
                    raise Conflict(
                        code="RECOVERY_CONTEXT_CHANGED",
                        message=(
                            "Recovery facts changed while the candidate was "
                            "being generated"
                        ),
                    )
                locked = self._lock_attempt(attempt.id)
                candidate = self._create_candidate(context, result)
                locked.candidate_delivery_plan_id = candidate.id
                locked.status = RecoveryPlanStatus.PENDING_REVIEW
                locked.solver_status = RecoverySolverStatus.FEASIBLE
                locked.validation_status = ValidationStatus.VALID
                locked.solver_validation_summary = {
                    "feasible": True,
                    "validation_issue_count": 0,
                    "deterministic": True,
                }
                # The legacy column is required by the fixed P0 schema. This is
                # a deterministic summary, not LLM/Agent generated content.
                locked.agent_explanation = explanation
                incident = self.incidents.lock_incident_by_id(incident_id)
                if incident is None:
                    raise NotFound(
                        code="INCIDENT_NOT_FOUND", message="Incident was not found"
                    )
                incident.status = IncidentStatus.REVIEW
                self.session.flush()
                created.append(self._view(locked))
            return StartRecoveryResult(
                incident_id=incident_id,
                outcome="PENDING_REVIEW",
                attempts_created=tuple(created),
                reviewable_recovery_plan_id=attempt.id,
                candidate_delivery_plan_id=candidate.id,
                agent_explanation=explanation,
                manual_intervention_required=False,
            )

    def _new_attempt(
        self,
        context: RecoveryContext,
        *,
        attempt_no: int,
        previous_id: UUID | None,
    ) -> RecoveryPlan:
        attempt = RecoveryPlan(
            id=uuid4(),
            recovery_code=(
                f"REC-{context.business_date:%Y%m%d}-"
                f"{uuid4().hex[:12].upper()}"
            ),
            incident_id=context.incident_id,
            attempt_no=attempt_no,
            previous_recovery_plan_id=previous_id,
            base_delivery_plan_id=context.base_plan_id,
            status=RecoveryPlanStatus.DRAFT,
            replanning_scope=context.scope,
            scope_description=self._scope_description(context.scope),
        )
        self.recoveries.add_recovery_plan(attempt)
        self.recoveries.flush()
        return attempt

    def _assert_base_still_current(
        self, incident_id: UUID, base_plan_id: UUID
    ) -> None:
        incident = self.incidents.lock_incident_by_id(incident_id)
        if incident is None:
            raise NotFound(code="INCIDENT_NOT_FOUND", message="Incident was not found")
        base = self.plans.lock_plan_by_id(base_plan_id)
        if (
            incident.delivery_plan_id != base_plan_id
            or base is None
            or base.status is not DeliveryPlanStatus.CURRENT
        ):
            raise Conflict(
                code="BASE_PLAN_NOT_CURRENT",
                message="Incident base delivery plan is no longer current",
            )

    def _lock_attempt(self, recovery_id: UUID) -> RecoveryPlan:
        attempt = self.recoveries.lock_recovery_plan_for_decision(recovery_id)
        if attempt is None:
            raise NotFound(
                code="RECOVERY_NOT_FOUND", message="Recovery attempt was not found"
            )
        return attempt

    def _create_candidate(
        self, context: RecoveryContext, result: SolverResult
    ) -> DeliveryPlan:
        self.plans.lock_business_date_for_planning(context.business_date)
        latest = self.plans.get_latest_plan_for_business_date(context.business_date)
        version_no = (latest.version_no if latest is not None else 0) + 1
        plan = DeliveryPlan(
            id=uuid4(),
            plan_code=f"PLAN-{context.business_date:%Y%m%d}-V{version_no}",
            plan_group_id=context.plan_group_id,
            business_date=context.business_date,
            version_no=version_no,
            parent_plan_id=context.base_plan_id,
            status=DeliveryPlanStatus.CANDIDATE,
            solver_engine="OR_TOOLS",
            validation_status=ValidationStatus.VALID,
            total_distance_meters=0,
            total_duration_seconds=0,
            vehicle_count=0,
            assigned_order_count=0,
            unassigned_order_count=0,
            validation_summary={
                "feasible": True,
                "validation_issue_count": 0,
                "recovery_scope": context.scope.value,
            },
            created_by="system:deterministic-recovery",
        )
        self.plans.add_delivery_plan(plan)
        self.plans.flush()

        route_by_order = self._persist_candidate_routes(plan, context, result)
        unassigned_by_order = {
            item.order_id: item for item in result.unassigned_orders
        }
        assigned_count = 0
        unassigned_count = 0
        for snapshot in context.plan_orders:
            route_id = route_by_order.get(snapshot.order_id)
            if route_id is not None:
                membership = DeliveryPlanOrder(
                    delivery_plan_id=plan.id,
                    order_id=snapshot.order_id,
                    assignment_status=PlanOrderAssignmentStatus.ASSIGNED,
                    vehicle_route_id=route_id,
                )
                assigned_count += 1
            else:
                solver_unassigned = unassigned_by_order.get(snapshot.order_id)
                reason_code = (
                    solver_unassigned.reason_code
                    if solver_unassigned is not None
                    else snapshot.unassigned_reason_code
                )
                reason_detail = (
                    solver_unassigned.reason_detail
                    if solver_unassigned is not None
                    else snapshot.unassigned_reason_detail
                )
                if reason_code is None:
                    raise BusinessError(
                        code="RECOVERY_CONTEXT_INVALID",
                        message="Candidate lost a base plan order assignment",
                    )
                membership = DeliveryPlanOrder(
                    delivery_plan_id=plan.id,
                    order_id=snapshot.order_id,
                    assignment_status=PlanOrderAssignmentStatus.UNASSIGNED,
                    vehicle_route_id=None,
                    unassigned_reason_code=reason_code,
                    unassigned_reason_detail=reason_detail,
                )
                unassigned_count += 1
            self.plans.add_delivery_plan_order(membership)

        routes = [item for item in self.session.new if isinstance(item, VehicleRoute)]
        plan.vehicle_count = len(routes)
        plan.assigned_order_count = assigned_count
        plan.unassigned_order_count = unassigned_count
        plan.total_distance_meters = sum(item.distance_meters for item in routes)
        plan.total_duration_seconds = sum(item.duration_seconds for item in routes)
        self.plans.flush()
        return plan

    def _persist_candidate_routes(
        self,
        plan: DeliveryPlan,
        context: RecoveryContext,
        result: SolverResult,
    ) -> dict[UUID, UUID]:
        solver_routes = {item.vehicle_id: item for item in result.routes}
        target_ids = {item.order_id for item in context.target_orders}
        rebuilt_route_ids = {
            item.original_route_id
            for item in context.target_orders
            if item.original_route_id is not None
        }
        vehicle_facts = {item.vehicle_id: item for item in context.vehicles}
        route_by_order: dict[UUID, UUID] = {}
        route_no = 0

        for base_route in context.routes:
            solver_route = solver_routes.pop(base_route.vehicle_id, None)
            preserved = (
                base_route.stops
                if base_route.id not in rebuilt_route_ids
                else tuple(
                    stop
                    for stop in base_route.stops
                    if stop.order_id not in target_ids
                    or stop.status == StopStatus.COMPLETED.value
                )
            )
            if not preserved and solver_route is None:
                continue
            route_no += 1
            route_id = uuid4()
            new_stops = solver_route.stops if solver_route is not None else ()
            end_location_id = (
                new_stops[-1].location_id
                if new_stops
                else preserved[-1].location_id
            )
            route = VehicleRoute(
                id=route_id,
                delivery_plan_id=plan.id,
                route_no=route_no,
                vehicle_id=base_route.vehicle_id,
                driver_id=base_route.driver_id,
                vehicle_driver_assignment_id=base_route.assignment_id,
                start_location_id=base_route.start_location_id,
                end_location_id=end_location_id,
                status=(
                    RouteStatus(base_route.status)
                    if solver_route is not None
                    else (
                        RouteStatus.COMPLETED
                        if base_route.id in rebuilt_route_ids
                        else RouteStatus(base_route.status)
                    )
                ),
                planned_start_at=base_route.planned_start_at,
                planned_end_at=(
                    context.current_time
                    + timedelta(seconds=new_stops[-1].departure_time_seconds)
                    if new_stops
                    else base_route.planned_end_at
                ),
                actual_start_at=base_route.actual_start_at,
                actual_end_at=(
                    base_route.actual_end_at if solver_route is None else None
                ),
                distance_meters=(
                    solver_route.distance_meters
                    if solver_route is not None
                    else base_route.distance_meters
                ),
                duration_seconds=(
                    solver_route.duration_seconds
                    if solver_route is not None
                    else base_route.duration_seconds
                ),
                vehicle_capacity_load_units_snapshot=(
                    vehicle_facts[base_route.vehicle_id].capacity_load_units
                    if base_route.vehicle_id in vehicle_facts
                    else base_route.capacity_load_units
                ),
                route_geometry=(
                    None if solver_route is not None else base_route.route_geometry
                ),
                route_metrics={
                    "recovery_scope": context.scope.value,
                    "preserved_stop_count": len(preserved),
                    "replanned_stop_count": len(new_stops),
                },
            )
            self.plans.add_vehicle_route(route)
            self._persist_stops(
                route_id,
                context,
                preserved,
                new_stops,
                route_by_order,
            )

        for solver_route in solver_routes.values():
            pair = vehicle_facts[solver_route.vehicle_id]
            route_no += 1
            route_id = uuid4()
            route = VehicleRoute(
                id=route_id,
                delivery_plan_id=plan.id,
                route_no=route_no,
                vehicle_id=pair.vehicle_id,
                driver_id=pair.driver_id,
                vehicle_driver_assignment_id=pair.assignment_id,
                start_location_id=pair.start_location_id,
                end_location_id=solver_route.stops[-1].location_id,
                status=RouteStatus.PLANNED,
                planned_start_at=pair.available_from,
                planned_end_at=context.current_time
                + timedelta(seconds=solver_route.stops[-1].departure_time_seconds),
                distance_meters=solver_route.distance_meters,
                duration_seconds=solver_route.duration_seconds,
                vehicle_capacity_load_units_snapshot=pair.capacity_load_units,
                route_metrics={
                    "recovery_scope": context.scope.value,
                    "preserved_stop_count": 0,
                    "replanned_stop_count": len(solver_route.stops),
                },
            )
            self.plans.add_vehicle_route(route)
            self._persist_stops(
                route_id,
                context,
                (),
                solver_route.stops,
                route_by_order,
            )
        return route_by_order

    def _persist_stops(
        self,
        route_id: UUID,
        context: RecoveryContext,
        preserved: tuple[StopSnapshot, ...],
        solver_stops,
        route_by_order: dict[UUID, UUID],
    ) -> None:
        copied_ids: dict[UUID, UUID] = {}
        origin_ids: dict[UUID, UUID] = {}
        sequence = 0
        for snapshot in preserved:
            sequence += 1
            stop_id = uuid4()
            stop = RouteStop(
                id=stop_id,
                vehicle_route_id=route_id,
                order_id=snapshot.order_id,
                location_id=snapshot.location_id,
                stop_type=StopType(snapshot.stop_type),
                sequence_no=sequence,
                precedence_stop_id=(
                    copied_ids.get(snapshot.precedence_stop_id)
                    if snapshot.precedence_stop_id is not None
                    else None
                ),
                source_incident_id=snapshot.source_incident_id,
                planned_arrival_at=snapshot.planned_arrival_at,
                planned_departure_at=snapshot.planned_departure_at,
                actual_arrival_at=snapshot.actual_arrival_at,
                actual_departure_at=snapshot.actual_departure_at,
                service_seconds=snapshot.service_seconds,
                time_window_start_at=snapshot.time_window_start_at,
                time_window_end_at=snapshot.time_window_end_at,
                demand_load_units_snapshot=snapshot.demand_load_units_snapshot,
                status=StopStatus(snapshot.status),
            )
            self.plans.add_route_stop(stop)
            copied_ids[snapshot.id] = stop_id
            if stop.stop_type is not StopType.DELIVERY:
                origin_ids[stop.order_id] = stop_id
            route_by_order[stop.order_id] = route_id

        orders = {item.order_id: item for item in context.target_orders}
        for solver_stop in solver_stops:
            sequence += 1
            order = orders[solver_stop.order_id]
            stop_id = uuid4()
            stop_type = StopType(solver_stop.stop_type.value)
            stop = RouteStop(
                id=stop_id,
                vehicle_route_id=route_id,
                order_id=solver_stop.order_id,
                location_id=solver_stop.location_id,
                stop_type=stop_type,
                sequence_no=sequence,
                precedence_stop_id=(
                    origin_ids[solver_stop.order_id]
                    if solver_stop.stop_type is SolverStopType.DELIVERY
                    else None
                ),
                source_incident_id=(
                    context.incident_id
                    if solver_stop.stop_type is SolverStopType.HANDOVER
                    else None
                ),
                planned_arrival_at=context.current_time
                + timedelta(seconds=solver_stop.arrival_time_seconds),
                planned_departure_at=context.current_time
                + timedelta(seconds=solver_stop.departure_time_seconds),
                service_seconds=solver_stop.service_duration_seconds,
                time_window_start_at=(
                    order.delivery_window_start_at
                    if solver_stop.stop_type is SolverStopType.DELIVERY
                    else (
                        context.current_time
                        if solver_stop.stop_type is SolverStopType.HANDOVER
                        else order.pickup_ready_at
                    )
                ),
                time_window_end_at=(
                    order.delivery_window_end_at
                    if solver_stop.stop_type is SolverStopType.DELIVERY
                    else None
                ),
                demand_load_units_snapshot=order.demand_load_units,
                status=StopStatus.PLANNED,
            )
            self.plans.add_route_stop(stop)
            if solver_stop.stop_type is not SolverStopType.DELIVERY:
                origin_ids[solver_stop.order_id] = stop_id
            route_by_order[solver_stop.order_id] = route_id

    @staticmethod
    def _initial_scope(incident_type: str) -> ReplanningScope:
        if incident_type == "VEHICLE_UNAVAILABLE":
            return initial_vehicle_unavailable_scope()
        if incident_type == "MERCHANT_DELAY":
            return initial_merchant_delay_scope()
        raise BusinessError(
            code="RECOVERY_CONTEXT_INVALID",
            message="Incident type is not recoverable in P0",
        )

    @staticmethod
    def _scope_description(scope: ReplanningScope) -> str:
        return {
            ReplanningScope.AFFECTED_ROUTE: "Replan the directly affected route.",
            ReplanningScope.CROSS_ROUTE: (
                "Allow available cross-route replacement resources."
            ),
            ReplanningScope.ALL_REMAINING: (
                "Replan all remaining eligible work in the base plan."
            ),
        }[scope]

    @staticmethod
    def _deterministic_summary(
        context: RecoveryContext, prior_infeasible_count: int
    ) -> str:
        prefix = (
            f"{prior_infeasible_count} narrower scope attempt(s) were infeasible. "
            if prior_infeasible_count
            else ""
        )
        return (
            f"{prefix}Deterministic {context.scope.value} replanning produced "
            f"a validated candidate for {len(context.target_orders)} order(s)."
        )

    @staticmethod
    def _view(attempt: RecoveryPlan) -> RecoveryAttemptView:
        return RecoveryAttemptView(
            recovery_plan_id=attempt.id,
            attempt_no=attempt.attempt_no,
            replanning_scope=attempt.replanning_scope.value,
            status=attempt.status.value,
            solver_status=(
                attempt.solver_status.value
                if attempt.solver_status is not None
                else None
            ),
            validation_status=(
                attempt.validation_status.value
                if attempt.validation_status is not None
                else None
            ),
            candidate_delivery_plan_id=attempt.candidate_delivery_plan_id,
        )

    @staticmethod
    def _view_data(view: RecoveryAttemptView) -> dict:
        return {
            "recovery_plan_id": str(view.recovery_plan_id),
            "status": view.status,
            "solver_status": view.solver_status,
            "validation_status": view.validation_status,
            "candidate_delivery_plan_id": (
                str(view.candidate_delivery_plan_id)
                if view.candidate_delivery_plan_id is not None
                else None
            ),
        }
