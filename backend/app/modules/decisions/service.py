from datetime import datetime, timezone
from sqlalchemy import select
from app.db.models import DeliveryPlan, RecoveryPlan
from app.integrations.agent.contracts import RecoveryError
from app.modules.planning.service import fingerprint
from app.modules.recovery.application import SqlRecoveryApplication


class DecisionService:
    def __init__(self, session_factory, clock=lambda: datetime.now(timezone.utc)):
        self.sessions, self.clock = session_factory, clock

    def decide(self, recovery_id, decision, reason, subject):
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
