"""Bounded cross-module execution. Model output never supplies business arguments."""
import logging
import re
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.integrations.agent.contracts import RecoveryError
from app.integrations.dispatch_agent.contracts import (
    DispatchCommand, DispatchContext, DispatchReply, IntentPlan, ToolObservation,
)
from app.integrations.dispatch_agent.planner import RuleIntentPlanner

logger = logging.getLogger(__name__)
UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
WRITE_ACTIONS = {"start_incident_recovery"}


class DispatchService:
    def __init__(self, queries, *, codec, planner=None, recovery=None,
                 timezone="Asia/Singapore", clock=None):
        self.queries, self.codec, self.planner = queries, codec, planner
        self.recovery = recovery
        self.timezone = ZoneInfo(timezone)
        self.clock = clock or (lambda: datetime.now(self.timezone))

    def run(self, command: DispatchCommand, subject: str, can_generate: bool = True):
        context, pending = self.codec.decode_state(command.context_token, subject) if command.context_token else (DispatchContext(), ())
        updates = command.context.model_dump(exclude_unset=True)
        # Changing the selected day must not silently reuse yesterday's incident/plan IDs.
        if "business_date" in updates and updates["business_date"] != context.business_date:
            context = DispatchContext()
        if "incident_id" in updates and updates["incident_id"] != context.incident_id:
            context = context.model_copy(update={"recovery_plan_id": None, "candidate_plan_id": None})
        if "recovery_plan_id" in updates and updates["recovery_plan_id"] != context.recovery_plan_id:
            context = context.model_copy(update={"base_plan_id": None, "candidate_plan_id": None})
        context = DispatchContext.model_validate({**context.model_dump(), **updates})
        context = self._extract_context(command.message, context, updates)
        rules = RuleIntentPlanner().plan(command.message, context)
        if rules.clarification and "/api/planning/generate" in rules.clarification:
            return self._reply("NEEDS_INPUT", rules.clarification, context, [], "rules", subject)
        # Resume only an answer containing explicit entity/date fields or a clear
        # continuation. A different task must not accidentally resume a mutation.
        answer_only = bool(re.fullmatch(
            r"(?:继续|就这个|今天|明天|(?:事件|异常|恢复方案|原计划|候选计划|incident_id|recovery_plan_id|base_plan_id|candidate_plan_id)\s*[:：=]?\s*" + UUID_PATTERN + r"|\d{4}-\d{2}-\d{2})", command.message.strip(), re.I))
        if pending and answer_only:
            rules = IntentPlan(actions=pending)
        plan, source = rules, "rules"
        if self.planner is not None and not (pending and answer_only and rules.actions):
            try:
                plan = IntentPlan.model_validate(self.planner.plan(command.message, context))
                source = "model"
                if plan.clarification:
                    plan = plan.model_copy(update={"clarification": "请明确需要执行的操作，并补充配送日期或事件、方案 ID。"})
            except Exception:
                # Model transport/schema failures never produce an executable guessed command.
                plan = IntentPlan(actions=tuple(a for a in rules.actions if a not in WRITE_ACTIONS),
                    clarification="意图模型暂不可用；可以查询，生成操作请使用业务接口或稍后重试。")
                source = "fallback"
        # A model can broaden read vocabulary but cannot introduce a write absent an
        # explicit command recognized by the deterministic write guard.
        if any(a in WRITE_ACTIONS and a not in rules.actions for a in plan.actions):
            plan = IntentPlan(actions=tuple(a for a in plan.actions if a not in WRITE_ACTIONS),
                              clarification="请明确说明要为哪个异常启动恢复。")
        if len(set(plan.actions)) != len(plan.actions):
            return self._reply("NEEDS_INPUT", "同一次请求不能重复执行同一业务动作。", context, [], source, subject)
        if not can_generate and any(a in WRITE_ACTIONS for a in plan.actions):
            raise RecoveryError("DISPATCH_FORBIDDEN", "当前身份只有查询权限", 403)
        order = ["get_delivery_status", "get_resource_availability",
                 "start_incident_recovery", "get_recovery_proposal", "compare_plan_versions"]
        actions = sorted(plan.actions, key=order.index)
        # A recovery result gives the recovery ID; proposal lookup supplies the
        # authoritative base/candidate pair before a requested comparison.
        if "start_incident_recovery" in actions and "compare_plan_versions" in actions:
            position = actions.index("compare_plan_versions")
            if "get_recovery_proposal" not in actions[:position]:
                actions.insert(position, "get_recovery_proposal")
        observations = []
        for index, action in enumerate(actions):
            # Never execute mutations while intent clarification is pending.
            if plan.clarification and action in WRITE_ACTIONS:
                continue
            missing = self._missing(action, context)
            if missing:
                return self._reply("NEEDS_INPUT", missing, context, observations, source, subject, actions[index:])
            try:
                data = self._execute(action, context, subject)
                observations.append(ToolObservation(tool=action, success=True, code="SUCCESS", data=data))
                context = self._update_context(action, context, data)
            except RecoveryError as exc:
                observations.append(ToolObservation(tool=action, success=False, code=exc.code))
                return self._reply("FAILED", str(exc), context, observations, source, subject)
            except Exception:
                logger.exception("Dispatch capability failed: %s", action)
                observations.append(ToolObservation(tool=action, success=False, code="DISPATCH_TOOL_FAILED"))
                return self._reply("FAILED", "业务能力执行失败，已停止后续步骤；请检查服务日志。", context, observations, source, subject)
        status = "NEEDS_INPUT" if plan.clarification or not observations else "COMPLETED"
        message = plan.clarification or self._summarize(observations)
        return self._reply(status, message, context, observations, source, subject)

    def _execute(self, action, context, subject):
        if action == "get_resource_availability":
            return self.queries.resources(context.business_date)
        if action == "get_delivery_status":
            return self.queries.operations(context.business_date)
        if action == "get_recovery_proposal":
            return self.queries.proposal(context.recovery_plan_id)
        if action == "compare_plan_versions":
            return self.queries.compare(context.base_plan_id, context.candidate_plan_id)
        if action == "start_incident_recovery":
            if self.recovery is None:
                raise RecoveryError("RECOVERY_NOT_CONFIGURED", "恢复事务服务和求解器未配置，未启动恢复", 503)
            reply = self.recovery.run(context.incident_id)
            if not reply.success:
                raise RecoveryError(reply.code, reply.message, reply.http_status)
            return {"outcome_code": reply.code, **reply.data}
        raise RecoveryError("DISPATCH_TOOL_FORBIDDEN", "未知调度工具", 422)

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
        labels = {"incident_id": r"(?:incident(?:_id)?|事件|异常)(?:\s*ID)?",
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
        if action in ("get_resource_availability", "get_delivery_status") and not context.business_date:
            return "请提供配送日期，例如 2026-09-25，或说明今天/明天。"
        if action == "start_incident_recovery" and not context.incident_id:
            return "请提供要恢复的 incident_id；请先选择异常，不会自动挑选或创建异常。"
        if action == "get_recovery_proposal" and not context.recovery_plan_id:
            return "请提供 recovery_plan_id，或先生成恢复方案。"
        if action == "compare_plan_versions" and not (context.base_plan_id and context.candidate_plan_id):
            return "请提供 base_plan_id 和 candidate_plan_id，或先查看恢复候选方案。"
        return None

    @staticmethod
    def _update_context(action, context, data):
        updates = {}
        if action == "start_incident_recovery":
            updates["recovery_plan_id"] = data.get("reviewable_recovery_plan_id")
            updates["candidate_plan_id"] = data.get("candidate_delivery_plan_id")
        if action == "get_recovery_proposal":
            updates = {"incident_id": data["incident_id"], "base_plan_id": data["base_plan"]["plan_id"],
                "candidate_plan_id": data["candidate_plan"]["plan_id"] if data.get("candidate_plan") else None}
        if action == "get_delivery_status" and data.get("current_plan"):
            updates["base_plan_id"] = data["current_plan"]["plan_id"]
        return DispatchContext.model_validate({**context.model_dump(), **updates})

    @staticmethod
    def _summarize(observations):
        messages = []
        for obs in observations:
            d = obs.data
            if obs.tool == "get_delivery_status":
                messages.append(f"订单 {d['order_count']} 个，风险订单 {d['at_risk_count']} 个，未解决异常 {d['open_incident_count']} 个。")
            elif obs.tool == "get_resource_availability":
                messages.append(f"有效车人绑定 {d['total']} 组，其中空闲 {d['idle_count']} 组。")
            elif obs.tool == "compare_plan_versions":
                messages.append(f"分配变化涉及 {d['changed_order_count']} 个订单，路线变化涉及 {d['changed_vehicle_count']} 辆车。")
            elif obs.tool == "start_incident_recovery":
                messages.append("恢复候选已生成，等待人工审核。" if d.get("outcome") == "PENDING_REVIEW" else "允许的恢复范围均未找到可行方案，需要人工处理。")
                if d.get("agent_explanation"):
                    messages.append(d["agent_explanation"])
            elif obs.tool == "get_recovery_proposal":
                messages.append(f"恢复尝试状态：{d['status']}。")
            else:
                messages.append(f"规划结果状态：{d.get('status', 'UNKNOWN')}；计划尚未自动生效。")
                if d.get("notice"):
                    messages.append(d["notice"])
        return "\n".join(messages)

    def _reply(self, status, message, context, observations, source, subject, pending_actions=()):
        return DispatchReply(status=status, message=message, context=context,
            observations=tuple(observations), planner_source=source,
            context_token=self.codec.encode(context, subject, pending_actions))
