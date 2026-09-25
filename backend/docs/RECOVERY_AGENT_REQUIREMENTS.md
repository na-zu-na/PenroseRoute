# Recovery Agent 功能核对与补全

核对依据：用户提供的《Recovery Agent 开发任务说明.md》。实现位置为外层 `PenroseRoute/backend`；内层同名目录未修改。

## 核对结果

原实现已具备核心恢复链路，但尚未完整满足说明中的解释与输入输出要求。另外，上层调度 Agent 原本可启动正常规划，与“正常规划不经过 Agent”不一致。本次补齐如下。

| 任务要求 | 修改前 | 当前实现 |
| --- | --- | --- |
| 正常规划不用 Agent | 上层意图工具仍允许生成正常计划 | 从 schema、执行器和依赖装配中移除；保留独立规划 API |
| 车辆不可用、商家延迟 | 已有确定性影响评估 | 保留；600 秒以内不进入恢复，601 秒触发 |
| 结构化 Recovery Context | 有影响、范围、时间和交接标记 | 增加事件摘要、当前计划指标、可用车人资源、历次尝试 |
| 受控 Tool 调用 | 已有，单次尝试缓存求解结果 | 保留；不提供 SQL、直接改派、范围修改或审批工具 |
| 固定范围升级 | 已有 | 仅 INFEASIBLE 扩围；ERROR/INVALID 停止 |
| 候选准入 | 已有 FEASIBLE + VALID 约束 | 保留；说明证据投影异常也停止，不保存不可信候选 |
| Dispatcher 解释 | 一段通用字符串 | 增加五项结构化解释，保留旧字符串兼容调用方 |
| 改派、未变任务、前后差异 | 未完整提供 | 业务层生成全计划证据，由 Agent 引用 |
| 交接、剩余风险 | 只含部分通用提示 | 包含订单 ID、冻结停靠点、交接待确认、历史风险和未分配订单 |
| 结构化终态 | 各分支字段不统一 | 成功、范围耗尽、求解失败、校验失败均返回明确状态和解释 |
| 模型初始化及故障处理 | 固定超时、初始化在回退边界外 | 延迟初始化、可配置 endpoint/超时、初始化或调用失败回退模板 |
| 查询已保存解释 | 只有原字符串 | JSONB 保存解释及证据，候选查询可读取 |
| Agent 与 DB/求解器隔离 | 已有 | 保留，并用导入边界测试禁止 ORM、业务模块、OR-Tools 依赖 |

## 执行与责任边界

```text
正常规划 API → PlanningService → OR-Tools → 独立校验 → 初始计划

异常 API → 确定性 Impact Assessment
  → 事务 A：物化输入与上下文，保存 DRAFT
  → RecoveryOrchestrator → 固定 LangGraph → ControlledTools
  → OR-Tools → 独立校验 → 全计划证据投影
  → Agent 模板解释 / 可选模型排序
  → 事务 B：校验快照，保存失败记录或完整 CANDIDATE
  → Dispatcher 审核
```

每次 LangGraph 只执行一次尝试。`RecoveryWorkflow` 在保存该尝试后调用确定性 `scope_after_result`，有下一范围才准备新的 DRAFT。范围循环放在业务 Workflow，避免 Agent 持有事务、Repository 或范围策略实现。这与任务说明要求的行为一致，未把数据库生命周期移进 LangGraph。

固定链为 `AFFECTED_ROUTE → CROSS_ROUTE → ALL_REMAINING`。只有 INFEASIBLE 可以继续；FEASIBLE + INVALID、ERROR、超时或契约错误均停止。成功候选仍需要人工批准，模型不能调用 Approve / Reject / Modify。

## 结构化输入及证据

`RecoveryContext` 包含事件及尝试 ID、日期、当前计划 ID、影响事实、冻结停靠点、事故位置、延迟秒数、当前时间、范围、快照令牌、行程来源，以及新增的：

- `incident_summary`：已由业务层整理的车辆/商家/延迟摘要。
- `incident_facts`：事件类型、车辆、商家、位置、延迟、备货时间和发现时间的结构化字段。
- `current_plan_summary`：计划版本、状态、订单与车辆数量、距离和时长。
- `available_resources`：当前范围允许使用的车人绑定、位置、容量和班次，不代表模型选择车辆。
- `previous_attempts`：此前尝试的 ID、序号、范围、求解与校验状态。

`modules/recovery/evidence.py` 只在结果 VALID 后运行。它将保留路线、完成前缀与新求解路线投影为完整候选，提供：

- `reassigned_orders`：订单、原车辆、新车辆。
- `changed_order_ids` / `unchanged_order_ids`：比较车辆、停靠顺序、位置、计划到离时间。数据库复制产生的新 UUID 不算业务变化。
- `handover_order_ids`：候选实际包含交接停靠点的订单。
- `before` / `after`：完整计划的分配数量、未分配数量、车辆数、米、秒。
- `changed_vehicle_ids`：路线停靠内容发生变化的车辆，包括路线移除和新增。
- `remaining_risks`：保留的未分配订单、待执行交接、输入中尚未解除的风险标记、估算行程及审核提醒。

“求解输出路线数”与“全计划实际变化车辆数”分开。原 `VerifiedSummary.changed_route_count` 字段暂时保留兼容性，当前求解器实际上在该字段返回输出路线数；解释不再将其误称为全计划变化路线数。恢复后的新路线与保留前缀在同一车辆上合并，时长从原路线开始时刻计算，保持与候选入库一致。

自定义后端若未提供全计划证据，解释明确标记不可用，不编造“零改派”或“无风险”。风险说明不等同于实时交通预测，未重新判断业务影响或放宽硬约束。

## 响应契约与前端读取

入口：`POST /api/incidents/{incident_id}/recovery`，body 为 `{}`。继续拒绝外部 `prompt`、`scope`、`vehicle_id` 等覆盖字段。

所有正常完成的恢复分支在 envelope 的 `data` 返回：

| 字段 | 内容 |
| --- | --- |
| status | PENDING_REVIEW / NO_FEASIBLE_RECOVERY / FAILED；是流程状态，不新增数据库枚举 |
| recovery_plan_id / attempt_no | 最终尝试 ID 和序号 |
| solver_status / validation_status | 求解状态与校验状态；未求得可行解时校验为 null |
| candidate_plan_id | 事务保存后的候选 ID；失败为 null |
| manual_intervention_required | 待审核成功及终止失败均为 true；成功表示需要人工审核 |
| explanation | 下列五项结构化解释 |
| recovery_evidence | 上述确定性证据；失败或自定义适配器未提供时为 null |
| explanation_source | template / model / fallback |
| diagnostic_codes / tool_trace | 故障代码与受控工具调用记录 |
| attempts_created | 当前命令创建的尝试历史 |

`explanation` 的五个字段为：

```json
{
  "summary": "事件摘要、求解和校验状态、人工审核要求",
  "impact_explanation": "受影响订单、已完成冻结任务、交接要求",
  "replanning_explanation": "当前尝试及确定性范围、改派、保持不变的订单",
  "result_explanation": "求解指标、完整计划前后差异、未分配订单",
  "remaining_risks": ["待现场确认的交接与仍存在的风险"]
}
```

旧字段 `agent_explanation`、`candidate_delivery_plan_id` 等继续保留。前端使用状态字段驱动按钮与流程，不解析解释字符串。范围耗尽仍沿用原业务码 `NO_FEASIBLE_RECOVERY` 与 HTTP 201；求解/校验失败 HTTP 500。

`GET /api/recovery-plans/{id}` 保留旧 `explanation` 字符串，同时增加 `structured_explanation`、`explanation_source`、`recovery_evidence`。历史记录若没有这些数据则返回 null。存储使用已有 `recovery_plans.solver_validation_summary` JSONB，不新增表或迁移。

## 模型接入

默认 `AGENT_EXPLANATION_PROVIDER=template`，无需模型凭据。使用 Bedrock 时：

```dotenv
AGENT_EXPLANATION_PROVIDER=bedrock
BEDROCK_MODEL_ID=配置支持Converse工具调用的模型或推理配置ID
AWS_REGION=ap-southeast-1
AGENT_CONNECT_TIMEOUT_SECONDS=3
AGENT_READ_TIMEOUT_SECONDS=10
# BEDROCK_ENDPOINT_URL=https://兼容Bedrock的端点
```

认证使用 AWS SDK 凭据链；若所用 SDK/服务支持 Bedrock API Key，可以在进程环境导出 `AWS_BEARER_TOKEN_BEDROCK`。此变量由 SDK 读取，仅写进应用 `.env` 不保证导出给 SDK。无需将密钥传入 Recovery Context、模型提示词或 HTTP 请求体。本客户端是 Bedrock Converse 协议，不是任意 OpenAI-compatible endpoint。

模型只能返回已知事实 ID 的排序；重复 ID、未知 ID、格式错误、超时、凭据或初始化失败均回退模板。模型遗漏的事实会由代码补回。结构化数值和业务状态全部来自确定性组件。

## 验证及限制

在 backend 目录执行：

```bash
python -m pytest tests/dispatch tests/agent tests/api tests/unit \
  tests/database/test_database_schema.py tests/database/test_database_bootstrap.py -q
```

覆盖任务要求的五种核心场景，以及 600/601 秒边界、模型回退、普通规划禁入 Agent、已完成取货与交接保留、全计划证据和实际落库指标一致、未变化及未分配订单、前缀合并统计、历史上下文、JSON 序列化和 HTTP 人工批准链路。

真实 OR-Tools 已参与测试；数据库应用测试使用隔离 SQLite，没有向用户数据库写测试数据。真实 PostgreSQL 的触发器、并发锁和实际 Bedrock 网络调用尚未验证。行程仍使用地理距离与固定车速估算；本次未新增前端或实时路况接入。
