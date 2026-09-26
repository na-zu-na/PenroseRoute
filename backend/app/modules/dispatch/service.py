"""Read-only dispatch coordinator; every capability returns detached facts."""
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import UUID
from app.core.errors import BusinessError
from app.integrations.agent.contracts import RecoveryError, ExplanationFact
from app.integrations.agent.explanation import arrange_facts
from app.integrations.dispatch_agent.contracts import DispatchCommand, DispatchContext, DispatchReply, IntentPlan, ToolObservation
from app.integrations.dispatch_agent.planner import RuleIntentPlanner, blocked_request, WRITE_NOTICE

logger = logging.getLogger(__name__)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


class DispatchService:
    def __init__(self, queries, *, codec, planner=None, explanation_client=None,
                 timezone="Asia/Singapore", clock=None):
        self.queries, self.codec, self.planner = queries, codec, planner
        self.explanation_client = explanation_client
        self.timezone = ZoneInfo(timezone)
        self.clock = clock or (lambda: datetime.now(self.timezone))

    def run(self, command: DispatchCommand, subject: str, can_generate: bool = False):
        # Both roles share the same read-only capability surface.
        context, pending = self.codec.decode_state(command.context_token, subject) if command.context_token else (DispatchContext(), ())
        updates = command.context.model_dump(exclude_unset=True)
        if "business_date" in updates and updates["business_date"] != context.business_date:
            context = DispatchContext()
        context = DispatchContext.model_validate({**context.model_dump(), **updates})
        context = self._extract_context(command.message, context, updates)
        rules = RuleIntentPlanner().plan(command.message, context)
        if blocked_request(command.message) or (rules.clarification and any(w in command.message.lower() for w in ("不要", "别", "如果", "若", "do not", "don't", "if "))):
            return self._reply("NEEDS_INPUT", rules.clarification or WRITE_NOTICE, context, [], "rules", subject)
        answer_only = bool(re.fullmatch(
            r"(?:继续|就这个|今天|明天|(?:订单|提醒|告警|恢复方案|恢复尝试|order_id|alert_id|recovery_plan_id)\s*[:：=]?\s*" + UUID_PATTERN + r"|\d{4}-\d{2}-\d{2})", command.message.strip(), re.I))
        plan, source = (IntentPlan(actions=pending) if pending and answer_only else rules), "rules"
        if self.planner is not None and not (pending and answer_only):
            try:
                plan = IntentPlan.model_validate(self.planner.plan(command.message, context))
                if len(set(plan.actions)) != len(plan.actions):
                    raise ValueError("duplicate actions")
                source = "model"
                if plan.clarification:
                    plan = IntentPlan(clarification="请明确查询对象及日期或 ID。")
            except Exception:
                plan, source = rules, "fallback"
        observations = []
        for index, action in enumerate(plan.actions):
            missing = self._missing(action, context)
            if missing:
                return self._reply("NEEDS_INPUT", missing, context, observations, source, subject, plan.actions[index:])
            try:
                data = self._execute(action, context)
                observations.append(ToolObservation(tool=action, success=True, code="SUCCESS", data=data))
            except (RecoveryError, BusinessError) as exc:
                observations.append(ToolObservation(tool=action, success=False, code=exc.code))
                return self._reply("FAILED", str(exc), context, observations, source, subject)
            except Exception:
                logger.exception("Read-only dispatch capability failed: %s", action)
                observations.append(ToolObservation(tool=action, success=False, code="DISPATCH_TOOL_FAILED"))
                return self._reply("FAILED", "查询暂不可用，请稍后重试。", context, observations, source, subject)
        if not observations:
            return self._reply("NEEDS_INPUT", plan.clarification or "请提供查询对象。", context, [], source, subject)
        # All query sessions have closed before this optional model call.
        facts = tuple(ExplanationFact(id=f"{obs.tool}:{f['id']}", text=f["text"])
                      for obs in observations for f in obs.data.get("facts", ()))
        arranged, explanation_source, diagnostics = arrange_facts(facts, self.explanation_client) if facts else ((), "template", ())
        message = "\n".join(f.text for f in arranged) or "查询完成；请查看结构化结果。"
        return self._reply("COMPLETED", message, context, observations, source, subject,
                           explanation_source=explanation_source, diagnostics=diagnostics)

    def _execute(self, action, context):
        if action == "get_delivery_status":
            return self.queries.operations(context.business_date)
        if action == "get_resource_availability":
            return self.queries.resources(context.business_date)
        if action == "get_recovery_proposal":
            return self.queries.proposal(context.recovery_plan_id)
        if action == "compare_plan_versions":
            return self.queries.compare(context.recovery_plan_id)
        if action == "explain_risk_alert":
            return self.queries.explain_alert(context.business_date, context.order_id, context.alert_id)
        raise RecoveryError("DISPATCH_TOOL_FORBIDDEN", "未知只读能力", 422)

    def _extract_context(self, text, context, explicit=None):
        values = {}
        dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
        if len(set(dates)) > 1:
            raise RecoveryError("DISPATCH_DATE_AMBIGUOUS", "一次请求请选择一个配送日期", 422)
        try:
            if dates:
                values["business_date"] = datetime.strptime(dates[0], "%Y-%m-%d").date()
            elif "今天" in text:
                values["business_date"] = self.clock().astimezone(self.timezone).date()
            elif "明天" in text:
                values["business_date"] = self.clock().astimezone(self.timezone).date() + timedelta(days=1)
        except ValueError as exc:
            raise RecoveryError("DISPATCH_DATE_INVALID", "日期无效", 422) from exc
        if values.get("business_date", context.business_date) != context.business_date:
            context = DispatchContext(business_date=values["business_date"], **{k: v for k, v in (explicit or {}).items() if k != "business_date"})
        labels = {"order_id": r"(?:order_id|订单)(?:\s*ID)?",
                  "alert_id": r"(?:alert_id|提醒|告警)(?:\s*ID)?","incident_id": r"(?:incident(?:_id)?|事件|异常)(?:\s*ID)?",
                  "recovery_plan_id": r"(?:recovery_plan_id|恢复方案|恢复尝试)(?:\s*ID)?",
                  "base_plan_id": r"(?:base_plan_id|基础计划|原计划)(?:\s*ID)?",
                  "candidate_plan_id": r"(?:candidate_plan_id|候选计划)(?:\s*ID)?"}
        for key, label in labels.items():
            matches = re.findall(label + r"\s*[:：=]?\s*(" + UUID_PATTERN + r")", text, re.I)
            if len(set(matches)) > 1:
                raise RecoveryError("DISPATCH_ENTITY_AMBIGUOUS", "同一类型对象出现多个 ID，请明确选择", 422)
            if matches:
                values[key] = UUID(matches[0])
        if "incident_id" in values and values["incident_id"] != context.incident_id:
            context = context.model_copy(update={"recovery_plan_id": None, "candidate_plan_id": None})
        if "recovery_plan_id" in values and values["recovery_plan_id"] != context.recovery_plan_id:
            context = context.model_copy(update={"base_plan_id": None, "candidate_plan_id": None})
        return DispatchContext.model_validate({**context.model_dump(), **values})


    @staticmethod
    def _missing(action, context):
        if action in ("get_delivery_status", "get_resource_availability", "explain_risk_alert") and not context.business_date:
            return "请提供配送日期，例如 2026-09-25。"
        if action in ("get_recovery_proposal", "compare_plan_versions") and not context.recovery_plan_id:
            return "请提供 recovery_plan_id；不会猜测候选或恢复尝试。"
        if action == "explain_risk_alert" and not (context.order_id or context.alert_id):
            return "请提供明确的 order_id 或 alert_id；不会根据同名对象猜测 ID。"
        return None

    def _reply(self, status, message, context, observations, source, subject, pending_actions=(),
               explanation_source="template", diagnostics=()):
        return DispatchReply(status=status, message=message, context=context,
            observations=tuple(observations), planner_source=source,
            explanation_source=explanation_source, diagnostic_codes=diagnostics,
            context_token=self.codec.encode(context, subject, pending_actions))
