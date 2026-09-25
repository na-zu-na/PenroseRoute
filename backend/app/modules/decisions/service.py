from datetime import datetime, timezone
from sqlalchemy import select
from app.db.models import DeliveryPlan, RecoveryPlan


class DecisionService:
    def __init__(self, session_factory, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.clock = session_factory, clock

    def decide(self, recovery_id, decision, reason, subject):
        from app.integrations.agent.contracts import RecoveryError
        from app.modules.planning.service import fingerprint
        from app.modules.recovery.application import SqlRecoveryApplication
        if decision not in ("APPROVE", "REJECT") or not reason.strip():
            raise RecoveryError("DECISION_INVALID", "需要有效的决定及原因", 422)
        with self.sessions() as session, session.begin():
            recovery = session.get(RecoveryPlan, recovery_id)
            if recovery is None:
                raise RecoveryError("RECOVERY_NOT_FOUND", "恢复方案不存在", 404)
            application = SqlRecoveryApplication(self.sessions)
            incident, base = application.lock(session, recovery.incident_id)
            session.refresh(recovery)
            if recovery.status != "PENDING_REVIEW":
                raise RecoveryError("RECOVERY_ALREADY_DECIDED", "恢复方案不是待审核状态", 409)
            candidate = session.get(DeliveryPlan, recovery.candidate_delivery_plan_id)
            if not candidate or candidate.status != "CANDIDATE" or candidate.validation_status != "VALID":
                raise RecoveryError("CANDIDATE_INVALID", "候选状态无效", 409)
            if candidate.parent_plan_id != base.id or recovery.base_delivery_plan_id != base.id:
                raise RecoveryError("BASE_PLAN_NOT_CURRENT", "候选与当前基础计划不一致", 409)
            now = self.clock()
            if decision == "APPROVE":
                summary = candidate.validation_summary or {}
                if "snapshot_token" not in summary:
                    raise RecoveryError("CANDIDATE_REVALIDATION_REQUIRED", "旧候选缺少快照校验信息，请重新生成", 409)
                at = datetime.fromisoformat(summary["snapshot_time"])
                fresh = application.snapshot(session, incident, base, summary["scope"], at)
                if fingerprint(fresh) != summary["snapshot_token"]:
                    raise RecoveryError("RECOVERY_SNAPSHOT_STALE", "配送事实已变化，请重新生成恢复方案", 409)
                # Revalidate remaining route schedule against the actual review time.
                from app.db.repositories.plan_repository import PlanRepository
                full = PlanRepository(session).get_plan_with_routes(candidate.id)
                from app.modules.planning.service import timestamp
                if any(timestamp(s.planned_arrival_at) < timestamp(now) for r in full.routes for s in r.stops if s.status != "COMPLETED"):
                    raise RecoveryError("CANDIDATE_SCHEDULE_STALE", "候选计划时间已过期，请重新规划后审核", 409)
                base.status, base.superseded_at = "SUPERSEDED", now
                session.flush()  # Release partial CURRENT unique index before activation.
                candidate.status, candidate.activated_at = "CURRENT", now
                incident.status, incident.resolved_at = "RESOLVED", now
            else:
                candidate.status = "CANCELLED"
                incident.status = "ASSESSING"
            recovery.status, recovery.dispatcher_decision = "DECIDED", decision
            recovery.reviewed_by, recovery.reviewed_at, recovery.decision_reason = subject, now, reason.strip()
            return {"recovery_plan_id": str(recovery.id), "decision": decision,
                    "candidate_plan_id": str(candidate.id), "candidate_status": str(candidate.status),
                    "current_plan_id": str(candidate.id if decision == "APPROVE" else base.id)}

    def modify(self, recovery_id, reason, subject, workflow):
        from app.integrations.agent.contracts import RecoveryError
        from app.modules.recovery.application import SqlRecoveryApplication
        import json
        from app.integrations.optimization.solver import estimated_matrix
        from app.modules.incidents.scope import next_scope
        from app.modules.recovery.orchestration import PreparedAttempt
        if not reason.strip():
            raise RecoveryError("DECISION_INVALID", "需要修改原因", 422)
        with self.sessions() as session, session.begin():
            recovery = session.get(RecoveryPlan, recovery_id)
            if recovery is None:
                raise RecoveryError("RECOVERY_NOT_FOUND", "恢复方案不存在", 404)
            application = SqlRecoveryApplication(self.sessions)
            incident, base = application.lock(session, recovery.incident_id)
            session.refresh(recovery)
            if recovery.status != "PENDING_REVIEW":
                raise RecoveryError("RECOVERY_ALREADY_DECIDED", "恢复方案不是待审核状态", 409)
            scope = next_scope(recovery.replanning_scope)
            if scope is None:
                raise RecoveryError("RECOVERY_SCOPE_EXHAUSTED", "已到最大恢复范围", 409)
            candidate = session.get(DeliveryPlan, recovery.candidate_delivery_plan_id)
            if not candidate or candidate.status != "CANDIDATE":
                raise RecoveryError("CANDIDATE_INVALID", "候选状态无效", 409)
            # Snapshot preparation and all decision writes share one transaction.
            context, snapshot = application.create_attempt(session, incident, base, recovery, scope, self.clock())
            candidate.status = "CANCELLED"
            recovery.status, recovery.dispatcher_decision = "DECIDED", "MODIFY"
            recovery.reviewed_by, recovery.reviewed_at, recovery.decision_reason = subject, self.clock(), reason.strip()
        snapshot["distance_matrix"], snapshot["duration_matrix"] = estimated_matrix(snapshot["locations"])
        return workflow.run(context.incident_id, prepared_after_modify=PreparedAttempt(context, json.dumps(snapshot)))


class DeterministicDecisionService:
    """P0 human decisions for the Agent-free Recovery workflow."""

    def __init__(self, session_factory, clock=lambda: datetime.now(timezone.utc)):
        self.sessions = session_factory
        self.clock = clock

    def decide(self, recovery_id, decision, reason, subject):
        from app.core.errors import BusinessError
        from app.db.models.planning import DeliveryPlanStatus
        from app.db.models.recovery import DispatcherDecision, IncidentStatus, RecoveryPlanStatus

        if decision not in ("APPROVE", "REJECT") or not reason.strip():
            raise BusinessError(code="DECISION_INVALID", message="A decision reason is required")
        with self.sessions() as session, session.begin():
            recovery, base, candidate, incident = self._lock_reviewable(session, recovery_id)
            now = self.clock()
            if decision == "APPROVE":
                base.status = DeliveryPlanStatus.SUPERSEDED
                base.superseded_at = now
                session.flush()  # The partial unique CURRENT index must be released first.
                candidate.status = DeliveryPlanStatus.CURRENT
                candidate.activated_at = now
                incident.status = IncidentStatus.RESOLVED
                incident.resolved_at = now
            else:
                candidate.status = DeliveryPlanStatus.CANCELLED
                incident.status = IncidentStatus.ASSESSING
            recovery.status = RecoveryPlanStatus.DECIDED
            recovery.dispatcher_decision = DispatcherDecision(decision)
            recovery.decision_reason = reason.strip()
            recovery.reviewed_by = subject
            recovery.reviewed_at = now
            return {
                "recovery_plan_id": str(recovery.id),
                "dispatcher_decision": decision,
                "reviewed_by": subject,
                "reviewed_at": now.isoformat(),
                "candidate_delivery_plan_id": str(candidate.id),
                "candidate_status": candidate.status.value,
                "base_delivery_plan_id": str(base.id),
                "base_status": base.status.value,
                "previous_delivery_plan_id": str(base.id),
                "current_delivery_plan_id": str(candidate.id if decision == "APPROVE" else base.id),
                "incident_status": incident.status.value,
            }

    def modify(self, recovery_id, reason, subject):
        from uuid import uuid4

        from app.core.errors import BusinessError, Conflict
        from app.db.models import RecoveryPlan
        from app.db.models.planning import DeliveryPlanStatus
        from app.db.models.recovery import DispatcherDecision, IncidentStatus, RecoveryPlanStatus
        from app.db.repositories.recovery_repository import RecoveryRepository
        from app.modules.incidents.scope import next_replanning_scope
        from app.modules.recovery.deterministic_workflow import RecoveryWorkflow

        if not reason.strip():
            raise BusinessError(code="DECISION_INVALID", message="A decision reason is required")
        with self.sessions() as session, session.begin():
            recovery, base, candidate, incident = self._lock_reviewable(session, recovery_id)
            next_scope = next_replanning_scope(recovery.replanning_scope)
            if next_scope is None:
                raise Conflict(
                    code="RECOVERY_SCOPE_EXHAUSTED",
                    message="No wider deterministic recovery scope is available",
                )
            attempts = RecoveryRepository(session).get_recovery_attempts(incident.id)
            if attempts[-1].id != recovery.id:
                raise Conflict(
                    code="RECOVERY_ALREADY_DECIDED",
                    message="A newer recovery attempt already exists",
                )
            now = self.clock()
            candidate.status = DeliveryPlanStatus.CANCELLED
            recovery.status = RecoveryPlanStatus.DECIDED
            recovery.dispatcher_decision = DispatcherDecision.MODIFY
            recovery.decision_reason = reason.strip()
            recovery.reviewed_by = subject
            recovery.reviewed_at = now
            incident.status = IncidentStatus.REPLANNING
            next_attempt = RecoveryPlan(
                id=uuid4(),
                recovery_code=f"REC-{base.business_date:%Y%m%d}-{uuid4().hex[:12].upper()}",
                incident_id=incident.id,
                attempt_no=recovery.attempt_no + 1,
                previous_recovery_plan_id=recovery.id,
                base_delivery_plan_id=base.id,
                status=RecoveryPlanStatus.DRAFT,
                replanning_scope=next_scope,
                scope_description=RecoveryWorkflow._scope_description(next_scope),
            )
            RecoveryRepository(session).add_recovery_plan(next_attempt)
            session.flush()
            next_attempt_id = next_attempt.id
            old_candidate_id = candidate.id
            old_attempt_id = recovery.id

        # The decision is durable; routing and OR-Tools run without a DB transaction.
        with self.sessions() as session:
            outcome = RecoveryWorkflow(session).resume(next_attempt_id)
        latest = outcome.attempts_created[-1]
        return {
            "decided_recovery_plan_id": str(old_attempt_id),
            "dispatcher_decision": "MODIFY",
            "cancelled_candidate_delivery_plan_id": str(old_candidate_id),
            "new_recovery_plan_id": str(latest.recovery_plan_id),
            "attempt_no": latest.attempt_no,
            "replanning_scope": latest.replanning_scope,
            "status": latest.status,
            "solver_status": latest.solver_status,
            "validation_status": latest.validation_status,
            "candidate_delivery_plan_id": (
                str(outcome.candidate_delivery_plan_id)
                if outcome.candidate_delivery_plan_id else None
            ),
            "outcome": outcome.outcome,
            "manual_intervention_required": outcome.manual_intervention_required,
        }

    @staticmethod
    def _lock_reviewable(session, recovery_id):
        from app.core.errors import Conflict, NotFound
        from app.db.models.planning import DeliveryPlanStatus, StopType, ValidationStatus
        from app.db.models.recovery import RecoveryPlanStatus, SolverStatus
        from app.db.repositories.incident_repository import IncidentRepository
        from app.db.repositories.plan_repository import PlanRepository
        from app.db.repositories.recovery_repository import RecoveryRepository

        recovery = RecoveryRepository(session).lock_recovery_plan_for_decision(recovery_id)
        if recovery is None:
            raise NotFound(code="RECOVERY_NOT_FOUND", message="Recovery plan was not found")
        if recovery.status is RecoveryPlanStatus.DECIDED:
            raise Conflict(code="RECOVERY_ALREADY_DECIDED", message="Recovery was already decided")
        if recovery.status is not RecoveryPlanStatus.PENDING_REVIEW:
            raise Conflict(code="RECOVERY_NOT_REVIEWABLE", message="Recovery is not pending review")
        plans = PlanRepository(session)
        base = plans.lock_plan_by_id(recovery.base_delivery_plan_id)
        candidate = (
            plans.lock_plan_by_id(recovery.candidate_delivery_plan_id)
            if recovery.candidate_delivery_plan_id else None
        )
        current = plans.get_current_plan(base.business_date) if base is not None else None
        if (
            base is None or base.status is not DeliveryPlanStatus.CURRENT
            or current is None or current.id != base.id
        ):
            raise Conflict(code="BASE_PLAN_NOT_CURRENT", message="Base plan is no longer current")
        if (
            candidate is None
            or candidate.status is not DeliveryPlanStatus.CANDIDATE
            or candidate.validation_status is not ValidationStatus.VALID
            or candidate.parent_plan_id != base.id
            or candidate.business_date != base.business_date
            or candidate.plan_group_id != base.plan_group_id
            or recovery.solver_status is not SolverStatus.FEASIBLE
            or recovery.validation_status is not ValidationStatus.VALID
        ):
            raise Conflict(code="CANDIDATE_PLAN_INVALID", message="Recovery candidate is not valid")
        incident = IncidentRepository(session).lock_incident_by_id(recovery.incident_id)
        if incident is None or incident.delivery_plan_id != base.id:
            raise Conflict(code="BASE_PLAN_NOT_CURRENT", message="Incident base plan changed")
        candidate_full = plans.get_plan_with_routes(candidate.id)
        base_full = plans.get_plan_with_routes(base.id)
        memberships = candidate_full.plan_orders
        routes = candidate_full.routes
        assigned = [item for item in memberships if item.assignment_status == "ASSIGNED"]
        unassigned = [item for item in memberships if item.assignment_status == "UNASSIGNED"]
        route_by_id = {route.id: route for route in routes}
        if (
            len(assigned) != candidate.assigned_order_count
            or len(unassigned) != candidate.unassigned_order_count
            or {item.order_id for item in memberships} != {item.order_id for item in base_full.plan_orders}
            or len(routes) != candidate.vehicle_count
            or candidate.total_distance_meters != sum(route.distance_meters for route in routes)
            or candidate.total_duration_seconds != sum(route.duration_seconds for route in routes)
            or candidate.validation_summary is None
            or any(
                membership.vehicle_route_id not in route_by_id
                or not any(
                    stop.order_id == membership.order_id and stop.stop_type is StopType.DELIVERY
                    for stop in route_by_id[membership.vehicle_route_id].stops
                )
                for membership in assigned
            )
        ):
            raise Conflict(
                code="CANDIDATE_PLAN_INVALID",
                message="Recovery candidate snapshot is inconsistent",
            )
        return recovery, base, candidate, incident
