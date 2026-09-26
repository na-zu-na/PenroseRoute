from langgraph.graph import END, START, StateGraph
from .client import ExplanationClient
from .contracts import AgentResult, ExplanationFact, ExplanationOutline, RecoveryExplanation
from .state import AgentState
from .tools import ControlledTools


def explanation_facts(context, observation):
    # Projections of rule/validator outputs, never incident classification by the LLM.
    facts = [
        ExplanationFact(id="incident", text=f"异常类型：{context.incident_type}。{context.incident_summary}；业务规则已标记需要重规划。"),
        ExplanationFact(id="scope", text=f"本次为第 {context.attempt_no} 次恢复尝试，规则指定范围为 {context.replanning_scope}。"),
        ExplanationFact(id="impact", text=f"规则快照包含 {len(context.affected_orders)} 项订单影响记录、{len(context.frozen_stop_ids)} 个冻结 Stop。"),
        ExplanationFact(id="solver", text=f"求解结果：{observation.solver.status}。"),
    ]
    if context.travel_time_source == "GEOGRAPHIC_ESTIMATE":
        facts.append(ExplanationFact(id="travel_source", text="距离和时长使用地理距离与固定车速估算，未使用真实道路或实时交通。"))
    if observation.validation is not None:
        facts.append(ExplanationFact(id="validation", text=f"确定性校验结果：{observation.validation.status}。"))
    if observation.solver.status == "FEASIBLE" and observation.validation.status == "VALID":
        s = observation.validation.summary
        evidence = observation.validation.recovery_evidence
        facts += [
            ExplanationFact(id="changes", text=f"本次求解已分配 {s.assigned_order_count} 个订单；求解输出路线数为 {s.changed_route_count}（不代表全计划实际变化路线数）。"),
            ExplanationFact(id="metrics", text=f"验证结果中的总距离为 {s.total_distance_meters} 米，总时长为 {s.total_duration_seconds} 秒，统计范围为本次重新求解的路线。"),
            ExplanationFact(id="unassigned", text=f"未分配订单数为 {len(s.unassigned_order_ids)}；订单 ID：{', '.join(map(str, s.unassigned_order_ids)) or '无'}。"),
            ExplanationFact(id="review", text="有效求解结果将交由业务层保存为候选，必须经 Dispatcher 人工批准才能生效；当前计划尚未切换。")]
        if evidence:
            facts.extend([
                ExplanationFact(id="reassigned", text="订单改派：" + ("；".join(f"{o.order_id}: {o.from_vehicle_id} → {o.to_vehicle_id}" for o in evidence.reassigned_orders) or "无")),
                ExplanationFact(id="unchanged", text="停靠顺序、位置、时间及车辆均保持不变的订单：" + (", ".join(map(str, evidence.unchanged_order_ids)) or "无")),
                ExplanationFact(id="comparison", text=(
                    f"全计划指标（前 → 后）：已分配 {evidence.before.assigned_order_count} → {evidence.after.assigned_order_count} 单；"
                    f"未分配 {evidence.before.unassigned_order_count} → {evidence.after.unassigned_order_count} 单；"
                    f"车辆 {evidence.before.vehicle_count} → {evidence.after.vehicle_count} 辆；"
                    f"距离 {evidence.before.total_distance_meters} → {evidence.after.total_distance_meters} 米；"
                    f"时长 {evidence.before.total_duration_seconds} → {evidence.after.total_duration_seconds} 秒；"
                    f"实际路线变化涉及 {len(evidence.changed_vehicle_ids)} 辆车。")),
                ExplanationFact(id="risks", text="剩余风险：" + "；".join(evidence.remaining_risks)),
            ])
        else:
            facts.append(ExplanationFact(
                id="risks",
                text=(
                    "当前阶段未提供全计划差异；候选持久化后由正式 U01 比较生成。"
                    "在此之前不推断订单改派、未变化任务或全计划指标。"
                ),
            ))
    elif observation.solver.status == "INFEASIBLE":
        facts.append(ExplanationFact(id="next", text="本次尝试不创建候选；是否存在下一范围由业务规则判断，Agent 不改变范围。"))
    else:
        facts.append(ExplanationFact(id="stop", text="本次尝试不创建候选，停止自动扩大范围，保留失败记录并要求人工处理。"))
    facts.extend([
        ExplanationFact(id="affected", text="订单影响（业务规则快照）：" + ("；".join(f"{o.order_id}: {o.impact_type}, 需重规划={o.requires_replanning}" for o in context.affected_orders) or "无")),
        ExplanationFact(id="handover", text="需交接订单（不代表已执行）：" + (", ".join(str(o.order_id) for o in context.affected_orders if o.handover_required) or "无")),
        ExplanationFact(id="frozen", text="冻结并保留已完成停靠点：" + (", ".join(map(str, context.frozen_stop_ids)) or "无")),
    ])
    return tuple(facts)


def run_agent(tools: ControlledTools, client: ExplanationClient | None = None) -> AgentResult:
    """One bounded graph per DRAFT attempt. Approval remains in Decisions module."""
    def read(state):
        context = tools.get_recovery_context()
        return {"context": context, "attempt_no": context.attempt_no, "replanning_scope": context.replanning_scope}

    def solve(state):
        tools.solve_replanning()
        return {}

    def result(state):
        observed = tools.get_solver_result()
        return {"observation": observed, "solver_status": observed.solver.status,
                "validation_status": observed.validation.status if observed.validation else None,
                "candidate_summary": observed.validation.summary if observed.validation else None}

    def explain(state):
        context, observed = state["context"], state["observation"]
        facts = explanation_facts(context, observed)
        ordered = [f.id for f in facts]
        source, diagnostics = "template", ()
        # Only explain verified feasible solutions using model assistance.
        if client is not None and observed.solver.status == "FEASIBLE" and observed.validation.status == "VALID":
            try:
                outline = ExplanationOutline.model_validate(client.arrange(facts))
                if len(set(outline.fact_ids)) != len(outline.fact_ids) or set(outline.fact_ids) - set(ordered):
                    raise ValueError("Unknown or repeated evidence ID")
                # Model cannot omit failed/unassigned/human-approval facts.
                ordered = list(outline.fact_ids) + [fid for fid in ordered if fid not in outline.fact_ids]
                source = "model"
            except Exception:
                source, diagnostics = "fallback", ("AGENT_EXPLANATION_FALLBACK",)
        lookup = {f.id: f.text for f in facts}
        explanation = "\n".join(lookup[fid] for fid in ordered)
        section = lambda ids: "\n".join(lookup[fid] for fid in ordered if fid in ids)
        evidence = observed.validation.recovery_evidence if observed.validation else None
        risks = evidence.remaining_risks if evidence else tuple(lookup[fid] for fid in ("travel_source", "risks", "next", "stop") if fid in lookup)
        structured = RecoveryExplanation(
            summary=section({"incident", "solver", "validation", "review", "next", "stop"}),
            impact_explanation=section({"impact", "affected", "handover", "frozen"}),
            replanning_explanation=section({"scope", "reassigned", "unchanged"}),
            result_explanation=section({"changes", "metrics", "unassigned", "comparison", "next", "stop"}),
            remaining_risks=risks)
        return {"result": AgentResult(recovery_plan_id=context.recovery_plan_id, observation=observed,
                agent_explanation=explanation, explanation_source=source, diagnostic_codes=diagnostics,
                tool_trace=tuple(tools.trace), explanation=structured), "explanation": structured,
                "diagnostic_codes": observed.solver.diagnostic_codes + (observed.validation.diagnostic_codes if observed.validation else ()) + diagnostics}

    graph = StateGraph(AgentState)
    for name, fn in [("read_context", read), ("run_solver", solve), ("read_result", result), ("explain", explain)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "read_context")
    graph.add_edge("read_context", "run_solver")
    graph.add_edge("run_solver", "read_result")
    graph.add_edge("read_result", "explain")
    graph.add_edge("explain", END)
    # No checkpointer/new DB tables; business attempts are the P0 durable lifecycle.
    return graph.compile().invoke({}, {"recursion_limit": 8})["result"]
