"""In-memory TEST FIXTURE. Not PostgreSQL, OR-Tools or real result validation."""
import json
from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from app.integrations.agent.contracts import (
    AttemptRecord, ImpactFact, RecoveryContext, RecoveryError,
    SolverResult, ValidationReport, VerifiedSummary)
from app.modules.recovery.context import validate_context
from app.modules.recovery.orchestration import PreparedAttempt


class FixtureApplication:
    def __init__(self, script=("INFEASIBLE", "VALID"), kind="VEHICLE_UNAVAILABLE", delay_seconds=900):
        self.script, self.kind, self.delay_seconds = script, kind, delay_seconds
        self.incident_id, self.base_id = uuid4(), uuid4()
        self.tx_active = False
        self.saved, self.drafts, self.calls, self.events = [], [], [], []
        self.current = True
        self.fact = ImpactFact(order_id=uuid4(), execution_status_snapshot="PICKED_UP" if kind == "VEHICLE_UNAVAILABLE" else "PLANNED",
            risk_status_snapshot="AT_RISK", requires_replanning=True, handover_required=kind == "VEHICLE_UNAVAILABLE",
            was_completed=False, impact_type="HANDOVER_REQUIRED" if kind == "VEHICLE_UNAVAILABLE" else "PICKUP_REPLAN")

    def prepare_attempt(self, incident_id, previous, next_scope):
        assert not self.tx_active
        self.tx_active = True
        try:
            if incident_id != self.incident_id:
                raise RecoveryError("INCIDENT_NOT_FOUND", http_status=404)
            if not self.current:
                raise RecoveryError("BASE_PLAN_NOT_CURRENT", http_status=409)
            if previous is None and self.saved and self.saved[-1].status == "PENDING_REVIEW":
                raise RecoveryError("RECOVERY_ALREADY_PENDING_REVIEW", http_status=409)
            context = RecoveryContext(recovery_plan_id=uuid4(), incident_id=incident_id,
                business_date=date(2026, 9, 25), base_delivery_plan_id=self.base_id,
                attempt_no=previous.attempt_no + 1 if previous else 1,
                previous_recovery_plan_id=previous.recovery_plan_id if previous else None,
                incident_type=self.kind, requires_replanning=True,
                replanning_scope=next_scope or "AFFECTED_ROUTE", scope_description="规则输出的固定范围",
                current_time=datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc), snapshot_token="fixture-v1",
                affected_orders=(self.fact,), frozen_stop_ids=(uuid4(),),
                incident_location_id=uuid4() if self.kind == "VEHICLE_UNAVAILABLE" else None,
                delay_seconds=self.delay_seconds if self.kind == "MERCHANT_DELAY" else None)
            validate_context(context)
            self.drafts.append(context)
            self.events.append(f"prepare:{context.attempt_no}")
            return PreparedAttempt(context, json.dumps({"fixture_only": True, "scope": context.replanning_scope,
                                                        "attempt_no": context.attempt_no}))
        finally:
            self.tx_active = False

    def solver(self, payload):
        assert not self.tx_active, "Solver executed inside transaction"
        data = json.loads(payload)
        self.calls.append(data["scope"])
        self.events.append(f'solve:{data["attempt_no"]}')
        value = self.script[min(data["attempt_no"] - 1, len(self.script) - 1)]
        if value == "TIMEOUT":
            raise TimeoutError()
        if value in {"INFEASIBLE", "ERROR"}:
            return SolverResult(status=value)
        return SolverResult(status="FEASIBLE", solution_payload_json=json.dumps({"fixture_only": True, "expected_validation": value}))

    def validator(self, payload, result):
        assert not self.tx_active, "Validator executed inside transaction"
        if json.loads(result.solution_payload_json)["expected_validation"] == "INVALID":
            return ValidationReport(status="INVALID", diagnostic_codes=("FIXTURE_CONSTRAINT_FAILURE",))
        return ValidationReport(status="VALID", summary=VerifiedSummary(assigned_order_count=2,
            changed_route_count=1, total_distance_meters=3500, total_duration_seconds=1200))

    def finish_attempt(self, prepared, result):
        assert not self.tx_active
        self.tx_active = True
        try:
            if not self.current:
                raise RecoveryError("BASE_PLAN_NOT_CURRENT", http_status=409)
            c, obs = prepared.context, result.observation
            saved = AttemptRecord(recovery_plan_id=c.recovery_plan_id, incident_id=c.incident_id,
                attempt_no=c.attempt_no, previous_recovery_plan_id=c.previous_recovery_plan_id,
                replanning_scope=c.replanning_scope, status="PENDING_REVIEW" if result.reviewable else "DRAFT",
                solver_status=obs.solver.status, validation_status=obs.validation.status if obs.validation else None,
                candidate_delivery_plan_id=uuid4() if result.reviewable else None,
                agent_explanation=result.agent_explanation)
            self.saved.append(saved)
            self.events.append(f"save:{c.attempt_no}")
            return saved
        finally:
            self.tx_active = False
