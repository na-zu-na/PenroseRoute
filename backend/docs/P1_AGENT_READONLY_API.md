# P1 Agent 只读入口与 Recovery 解释

## 配置

在 backend/.env 设置身份令牌与不少于 32 字符的上下文密钥：

```dotenv
DISPATCH_API_TOKENS='{"YOUR_LONG_RANDOM_TOKEN":{"subject":"dispatcher-1","role":"dispatcher"}}'
DISPATCH_CONTEXT_SECRET=YOUR_RANDOM_SECRET_AT_LEAST_32_CHARACTERS
DISPATCH_INTENT_PROVIDER=rules
AGENT_EXPLANATION_PROVIDER=template
RECOVERY_ORCHESTRATION_MODE=deterministic
```

reader 与 dispatcher 在对话入口都只能读取。模型为可选能力；无模型时规则和模板可以完整返回查询事实。Bedrock 接入沿用 BEDROCK_MODEL_ID、AWS_REGION 与 AWS 凭据链；Recovery 启用 Agent 需把 RECOVERY_ORCHESTRATION_MODE 改为 agent。

## 对话请求

```http
POST /api/agent/dispatch
Authorization: Bearer YOUR_LONG_RANDOM_TOKEN
Content-Type: application/json
```

```json
{"message":"2026-09-25 运营情况"}
```

支持：

- “查看今天风险和空闲车辆”。
- “查看候选并比较差异”，context 传 recovery_plan_id。
- “为什么订单 <UUID> 有风险”，context 传 business_date。
- “这条提醒 <UUID> 为什么出现”，context 传 business_date。

后两项目前返回 ALERT_QUERY_UNAVAILABLE，因为正式 U06 提醒查询契约尚未交付。不要把这一响应解释为无风险或无提醒。

缺日期或 ID 返回 NEEDS_INPUT。下一次提交 context_token，并在 message 中回答日期、明确标注的对象 ID 或“继续”；也可通过 context 提供选择。签名上下文仅保存受控选择和待完成查询，不保存聊天文本。更换日期清除上一日期的对象选择。

```json
{"message":"查看候选并比较差异","context":{"recovery_plan_id":"<UUID>"}}
```

比较只接受 Recovery 关联的单个 Candidate，不使用任意 Base/Candidate 的第二套差值算法。历史比较会标为审计用途，不声称其仍待审批或仍为当前计划。

## 返回口径

沿用 success/code/message/data/request_id。data 中：

- status：COMPLETED / NEEDS_INPUT / FAILED。
- planner_source：rules / model / fallback，指意图识别。
- explanation_source：template / model / fallback，指可信事实排序。
- diagnostic_codes：排序失败的回退诊断。
- observations：每项只读服务返回的物化事实、as_of、facts、truncated、missing_reasons。
- context/context_token：下一轮受控对象选择。

运营快照的 AT_RISK 数与持久化活动提醒数具有不同口径和时点。当前 U06 查询未接入时，后者是 null 而不是 0。

“生成正常计划/启动恢复/批准/修改/执行 SQL”等请求只返回正式 API 提示，不执行写操作。正常规划、异常创建、Recovery 和审批继续使用原业务接口。

## Recovery 模型调用时序

```mermaid
sequenceDiagram
    participant API as Recovery API
    participant W as Recovery Workflow
    participant G as 固定 Graph / OR-Tools
    participant DB as PostgreSQL
    participant M as 可选解释模型
    API->>W: incident_id
    W->>DB: 短事务：物化上下文并保存 DRAFT
    W->>G: 单次求解与独立校验
    G-->>W: FEASIBLE + VALID
    W->>DB: 短事务：复核快照，保存 Candidate、U01 证据、模板
    DB-->>W: 提交完成，PENDING_REVIEW
    W->>M: 事务外发送完整 U01 可信事实
    M-->>W: 事实 ID 的精确排列或失败
    W->>DB: 短事务：复核状态/快照，只保存解释及来源
    W-->>API: 已持久化解释；写入失败保留模板
```

模型失败不会重试求解，解释更新失败不会回滚已提交 Candidate。模型等待期间被审核的方案不会被解释更新覆盖。最终批准仍由独立人工接口完成。
