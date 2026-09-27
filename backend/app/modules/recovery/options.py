"""Bounded, deterministic alternative generation and auditable ranking.

All options use the same operational time and base plan. LLM output never
controls feasibility, priority, or human approval.
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from app.core.errors import Conflict, IntegrationError
from app.db.models.planning import DeliveryPlanStatus, ValidationStatus
from app.db.models.recovery import IncidentStatus, RecoveryPlanStatus, ReplanningScope, SolverStatus as DBStatus
from app.integrations.optimization.contracts import SolverStatus
from app.integrations.optimization.ortools_solver import ORToolsSolver
from app.integrations.optimization.result_validator import SolverResultValidator
from app.integrations.routing.distance_matrix import build_distance_time_matrix
from app.modules.recovery.deterministic_context import materialize_recovery_context
from app.modules.recovery.deterministic_orchestration import build_recovery_solver_input
from app.modules.recovery.deterministic_workflow import RecoveryWorkflow
from app.modules.recovery.review_schedule import review_deadline
from app.modules.planning.comparison import compare_plan_snapshots

REVIEW_BUFFER_SECONDS = 120

RANKING_POLICY = 'COVERAGE_DISRUPTION_COMPLETION_V1'
RANKING_CRITERIA = ('unassigned_order_count', 'reassigned_order_count',
                    'changed_order_count', 'handover_count', 'completion_at')


def priority_key(metrics):
    return tuple(metrics[key] if metrics[key] is not None else '9999' for key in RANKING_CRITERIA)


def fingerprint(context):
    raw = json.dumps(jsonable_encoder(asdict(context)), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def solve_alternatives(source, *, max_candidates=3):
    """Explore resource subsets, preserving mandatory onboard/frozen vehicles.

    Each solve has the existing OR-Tools time limit. At most 3 solves per scope.
    Removing a used optional vehicle forces a genuinely different allocation.
    """
    if not 1 <= max_candidates <= 3:
        raise ValueError('max_candidates must be between 1 and 3')
    required = {o.required_vehicle_id for o in source.orders if o.required_vehicle_id}
    required.update(t.vehicle_id for t in source.frozen_tasks)
    queue, seen, results, signatures = [frozenset()], set(), [], set()
    calls = 0
    while queue and calls < 3 and len(results) < max_candidates:
        excluded = queue.pop(0)
        if excluded in seen:
            continue
        seen.add(excluded)
        problem = replace(source, vehicles=tuple(v for v in source.vehicles if v.vehicle_id not in excluded))
        if not required.issubset({v.vehicle_id for v in problem.vehicles}):
            continue
        calls += 1
        result = ORToolsSolver().solve(problem)
        if result.status is SolverStatus.ERROR:
            raise IntegrationError(code='RECOVERY_SOLVER_ERROR', message='Alternative generation failed')
        if result.status is not SolverStatus.FEASIBLE or result.unassigned_orders:
            continue
        if SolverResultValidator().validate(problem, result) or SolverResultValidator().validate(source, result):
            raise IntegrationError(code='RECOVERY_VALIDATION_FAILED', message='Alternative failed constraint validation')
        signature = tuple(sorted((str(r.vehicle_id), tuple((str(s.order_id), s.stop_type.value, str(s.location_id)) for s in r.stops)) for r in result.routes))
        if signature not in signatures:
            signatures.add(signature)
            results.append(result)
        for vehicle_id in sorted({r.vehicle_id for r in result.routes} - required, key=str):
            candidate = excluded | {vehicle_id}
            if candidate not in seen:
                queue.append(candidate)
    return tuple(results)


def plan_signature(plan):
    """Ignore plan IDs and small ETA differences when deduplicating business actions."""
    return tuple(sorted((str(r.vehicle_id), tuple(
        (str(s.order_id), str(s.stop_type), str(s.location_id))
        for s in sorted(r.stops, key=lambda s: s.sequence_no))) for r in plan.routes))


class RecoveryOptionsWorkflow(RecoveryWorkflow):
    def __init__(self, session, *, max_candidates=3, **kwargs):
        super().__init__(session, **kwargs)
        if not 1 <= max_candidates <= 3:
            raise ValueError('max_candidates must be between 1 and 3')
        self.max_candidates = max_candidates

    def start(self, incident_id, *, request_id="-", regenerate=False):
        from sqlalchemy import text
        from app.core.errors import NotFound
        from app.db.models.recovery import DispatcherDecision
        with self.session.begin():
            self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                 {"key": incident_id.int % (2**63 - 1)})
            incident = self.incidents.lock_incident_by_id(incident_id)
            if incident is None:
                raise NotFound(code="INCIDENT_NOT_FOUND", message="Incident was not found")
            existing = self.recoveries.get_recovery_attempts(incident_id)
            if any(a.status == RecoveryPlanStatus.DRAFT and a.solver_status is None for a in existing):
                raise Conflict(code="RECOVERY_ALREADY_IN_PROGRESS", message="Recovery generation is already running")
            if any(a.status == RecoveryPlanStatus.PENDING_REVIEW
                   and not (a.solver_validation_summary or {}).get("options") for a in existing):
                raise Conflict(code="LEGACY_RECOVERY_PENDING",
                               message="Decide the existing single candidate before starting options")
            if existing and not regenerate:
                raise Conflict(code="RECOVERY_ALREADY_PENDING_REVIEW", message="Use persisted options or explicitly regenerate")
            context = materialize_recovery_context(self.session, incident_id=incident_id,
                scope=self._initial_scope(incident.incident_type.value))
            for old in existing:
                if old.status == RecoveryPlanStatus.PENDING_REVIEW:
                    old.status = RecoveryPlanStatus.DECIDED
                    old.dispatcher_decision = DispatcherDecision.REJECT
                    old.decision_reason = "Automatically closed for explicit regeneration"
                    old.reviewed_by = "system:recovery-options"
                    old.reviewed_at = datetime.now(timezone.utc)
                    old.solver_validation_summary = {**(old.solver_validation_summary or {}),
                        "automatic_closure": "REGENERATED"}
                    old.candidate_delivery_plan.status = DeliveryPlanStatus.CANCELLED
            attempt = self._new_attempt(context,
                attempt_no=existing[-1].attempt_no + 1 if existing else 1,
                previous_id=existing[-1].id if existing else None)
            attempt.solver_validation_summary = {"options_generation": True}
            incident.status = IncidentStatus.REPLANNING
        return self._execute_attempts(incident_id, context, attempt, request_id)

    def _execute_attempts(self, incident_id, context, attempt, request_id):
        # The inherited start/resume reserves a DRAFT before releasing DB locks.
        # No candidate is made reviewable until the whole batch is committed.
        try:
            # Leave a short, explicit decision window before replanned work starts.
            with self.session.begin():
                context = materialize_recovery_context(self.session, incident_id=incident_id,
                    scope=context.scope,
                    operational_time=context.current_time + timedelta(seconds=REVIEW_BUFFER_SECONDS))
            return self._generate(incident_id, context, attempt)
        except Exception:
            self.session.rollback()
            with self.session.begin():
                locked = self._lock_attempt(attempt.id)
                if locked.candidate_delivery_plan_id is None:
                    locked.solver_status = DBStatus.ERROR
                    locked.solver_validation_summary = {'failure_stage': 'option_generation', 'manual_intervention_required': True}
            raise

    def _generate(self, incident_id, initial_context, reserved):
        contexts = [initial_context]
        with self.session.begin():
            for scope in ReplanningScope:
                if list(ReplanningScope).index(scope) > list(ReplanningScope).index(initial_context.scope):
                    contexts.append(materialize_recovery_context(self.session,
                        incident_id=incident_id, scope=scope, operational_time=initial_context.current_time))
        solved = []
        # At most 9 solver calls, always outside the DB transaction.
        for context in contexts:
            source = build_recovery_solver_input(context, build_distance_time_matrix(context.locations))
            for result in solve_alternatives(source, max_candidates=self.max_candidates):
                solved.append((context, result))
        batch_id = str(uuid4())
        rows, signatures = [], set()
        with self.session.begin():
            self._assert_base_still_current(incident_id, initial_context.base_plan_id)
            self.plans.lock_recovery_execution_facts(initial_context.base_plan_id,
                extra_vehicle_ids=tuple({v.vehicle_id for c in contexts for v in c.vehicles}),
                extra_driver_ids=tuple({v.driver_id for c in contexts for v in c.vehicles}),
                extra_assignment_ids=tuple({v.assignment_id for c in contexts for v in c.vehicles}))
            self.session.expire_all()
            for context in contexts:
                fresh = materialize_recovery_context(self.session, incident_id=incident_id,
                    scope=context.scope, operational_time=context.current_time)
                if fresh != context:
                    raise Conflict(code='RECOVERY_CONTEXT_CHANGED', message='Recovery facts changed during option generation')
            locked = self._lock_attempt(reserved.id)
            previous = locked
            for context, result in solved:
                # Roll back duplicate snapshots rather than leave orphan candidate plans.
                with self.session.begin_nested() as savepoint:
                    candidate = self._create_candidate(context, result)
                    snapshot = self.plans.get_plan_with_routes(candidate.id)
                    signature = plan_signature(snapshot)
                    if signature in signatures:
                        savepoint.rollback()
                        continue
                    base = self.plans.get_plan_with_routes(context.base_plan_id)
                    comparison = compare_plan_snapshots(recovery_plan_id=locked.id,
                        comparison_at=context.current_time, base=base, candidate=snapshot, reviewable=True)
                    remaining = [s for r in snapshot.routes for s in r.stops if str(s.status) != 'COMPLETED']
                    finish = max((s.planned_departure_at for s in remaining), default=context.current_time)
                    deadline = review_deadline(context, snapshot)
                    metrics = dict(unassigned_order_count=candidate.unassigned_order_count,
                        reassigned_order_count=comparison.reassigned_order_count,
                        changed_order_count=sum(o.route_task_changed for o in comparison.orders),
                        handover_count=sum(str(s.stop_type) == 'HANDOVER' for s in remaining),
                        completion_at=finish.astimezone(timezone.utc).isoformat())
                    if rows:
                        current = self._new_attempt(context, attempt_no=previous.attempt_no + 1, previous_id=previous.id)
                    else:
                        current = locked
                        current.replanning_scope = context.scope
                        current.scope_description = self._scope_description(context.scope)
                    current.candidate_delivery_plan_id = candidate.id
                    current.solver_status = DBStatus.FEASIBLE
                    current.validation_status = ValidationStatus.VALID
                    current.status = RecoveryPlanStatus.PENDING_REVIEW
                    current.agent_explanation = (
                        f'通过约束校验；改派 {metrics["reassigned_order_count"]} 单，'
                        f'任务调整 {metrics["changed_order_count"]} 单，交接 {metrics["handover_count"]} 次。'
                        '预计时间来自计划快照，须经调度员批准后生效。')
                    current.solver_validation_summary = {'feasible': True, 'validation_issue_count': 0,
                        'options': {'batch_id': batch_id, 'ranking_policy': RANKING_POLICY,
                            'metrics': metrics, 'review_deadline_at': deadline.isoformat(), 'strategy': f'{context.scope.value}_RESOURCE_ALTERNATIVE',
                            'operational_time': context.current_time.isoformat(), 'context_fingerprint': fingerprint(context)}}
                    self.session.flush()
                    signatures.add(signature)
                    rows.append(current)
                    previous = current
            # Rank complete snapshots, so metrics remain comparable across scopes.
            rows.sort(key=lambda r: (priority_key(r.solver_validation_summary['options']['metrics']), r.attempt_no))
            for i, row in enumerate(rows):
                summary = dict(row.solver_validation_summary)
                option = dict(summary['options'])
                option['priority'] = i + 1
                option['ranking_reason'] = '按未分配订单、改派订单、任务调整、交接次数、预计完成时间依次升序比较'
                if i >= self.max_candidates:
                    # Keep validated search results for audit, never as human
                    # rejections or reviewable candidates.
                    row.status = RecoveryPlanStatus.DRAFT
                    self.plans.lock_plan_by_id(row.candidate_delivery_plan_id).status = DeliveryPlanStatus.CANCELLED
                    option['filtered_by_limit'] = True
                summary['options'] = option
                row.solver_validation_summary = summary
            incident = self.incidents.lock_incident_by_id(incident_id)
            incident.status = IncidentStatus.REVIEW if rows else IncidentStatus.ASSESSING
            if not rows:
                locked.solver_status = DBStatus.INFEASIBLE
                locked.solver_validation_summary = {'diagnostic': 'No distinct feasible alternatives found', 'options_batch_id': batch_id}
            self.session.flush()
            from app.modules.recovery.option_queries import list_options
            return list_options(self.session, incident_id)
