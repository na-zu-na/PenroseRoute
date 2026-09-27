# 多候选恢复与人工选择

## 接入方式

新增接口，不替换旧的单候选 `POST /api/incidents/{incident_id}/recovery`。
现有项目只有后端代码；前端需切换到以下多候选入口。

1. `POST /api/incidents/{incident_id}/recovery-options`，调度员权限。
   请求 `{"max_candidates":3}`，范围为 1–3，省略时为 3。
2. `GET /api/incidents/{incident_id}/recovery-options`，读取最近一批持久化结果。
3. 对每个候选的 `recovery_plan_id` 调用现有
   `GET /api/recovery-plans/{id}/comparison`，获得与同一基础计划的详细差异。
4. 调度员选择任意 `reviewable=true` 的候选，调用现有
   `POST /api/recovery-plans/{id}/approve`，请求
   `{"decision_reason":"选择此方案的原因"}`。
   第一名只是推荐，不会自动执行。

返回 `data` 的主要字段：

| 字段 | 用途 |
| --- | --- |
| `batch_id` | 一次候选集合的标识，重新生成后变化 |
| `outcome` | `NOT_STARTED`、`GENERATING`、`PENDING_REVIEW`、`NO_FEASIBLE_RECOVERY`、`FAILED`、`DECIDED`，或旧流程的 `LEGACY_RECOVERY` |
| `candidates` | 按 `priority` 升序排列的候选列表；历史决策后仍可查看 |
| `recommended_recovery_plan_id` | 当前仍可审核的最高优先级候选，全部失效时为 null |
| `ranking_policy` | 当前策略 `COVERAGE_DISRUPTION_COMPLETION_V1` |
| `manual_intervention_required` | 是否需要人工进一步处理 |

每个候选包括原详情字段，以及 `priority`、`reviewable`、`strategy`、
`ranking_reason`、`metrics`。每个候选都有自己的 Recovery ID 和配送计划 ID。
请按 ID 提交选择，不按数组位置或标签 A/B/C 提交。

## 生成和排序

适用于原来支持的车辆不可用、商家延迟两类 Incident。
固定同一个原计划版本和业务快照时点，依次评估受影响路线、跨路线、全部剩余任务范围。
每个范围先求解，再排除已使用的可替代车辆，寻找不同运力安排。
已上车任务绑定的车辆和冻结任务车辆不排除，容量、时间窗、取送顺序等约束不放宽。
每个范围最多 3 次求解，总共最多 9 次；每次沿用现有求解器时间上限。
此搜索有界，不声称穷举或全局最优，不保证每次凑齐三个方案。

所有进入人工审核的候选都必须完成范围内订单分配并通过校验。
相同车辆、订单、站点及执行顺序的方案去重，不把仅 ETA 略有变化的方案当作新选项。
跨范围候选按照完整持久化计划比较，不混用不同范围的求解里程。

排序采用以下字段的字典序，数值越小越优先：

1. `unassigned_order_count`：完整候选计划未分配订单数，优先覆盖更多任务。
2. `reassigned_order_count`：与原计划比较的改派数。
3. `changed_order_count`：任务发生变化的订单数。
4. `handover_count`：未完成的交接站点数。
5. `completion_at`：候选计划未完成站点的最晚计划离开时间，UTC。
6. 同分按生成顺序稳定排序。

这是一套公开的默认业务偏好，不是大模型主观打分。前端应显示原始指标和排序理由。
`completion_at` 是计划值，不是实测完成时间或服务保证；不存在可比剩余里程时，不展示节省里程百分比。
`strategy` 表示搜索范围和运力替代方式，不应误标为“最低成本”等未经优化目标保证的名称。

## 人工决策和并发

- 批准任意候选：在同一事务中切换 Current Plan、解决 Incident，并关闭其他待审核候选。
- 关闭的其他候选记录 `automatic_closure`，与调度员主动拒绝区分；兼容现有决策枚举，存为 REJECT。
- 拒绝一个候选：其他候选仍可选择，Incident 保持 REVIEW；最后一个被拒绝后进入 ASSESSING。
- 修改任意候选：若还有更大范围，关闭旧集合，按扩大范围重新生成候选；没有更大范围时返回 409，原集合保留。
- `POST .../recovery-options` 请求增加 `"regenerate":true`：显式关闭旧候选，从最新业务快照重新生成，可用于候选过期、全部拒绝或失败后重试。
- 重复生成或仍有未完成生成任务时返回冲突。刷新页面使用 GET，不重复 POST。
- 同一 Incident 的多候选决策通过 PostgreSQL 事务锁串行化，防止不同调度员同时选中不同方案。
- 审批前重新核对候选对应的资源/订单快照，变化时返回 `RECOVERY_CONTEXT_CHANGED`。
- 生成过程中无候选可供审核；整个集合一次提交。失败不会发布半套可选方案，也不会回显旧集合为当前集合。

## 数据和模式

复用 `recovery_plans`、`delivery_plans`，排序与批次元数据保存在
`solver_validation_summary.options`，无需数据库迁移。
未进入数量上限的有效搜索结果保留为 DRAFT + CANCELLED 计划以便审计，不出现在 candidates 中。
旧单候选仍待审核时不能直接启动多候选（`LEGACY_RECOVERY_PENDING`）；先拒绝旧候选，再显式重新生成。
原始 recovery-plans 列表仍是尝试历史，不要把其中所有记录当作可选方案。

多候选入口使用确定性 OR-Tools 和规则排序，不调用大模型决定优先级。
现有只读对话可继续通过明确的 Recovery ID 解释每个候选及其方案对比；对话本身不执行审批。

## 验证

新增测试涵盖真实求解器的替代方案、资源不足、不可行过滤、优先级，
以及 PostgreSQL HTTP 流程、非首选审批、单个拒绝、上下文过期、重新生成、
修改后失败、真实并发选择。数据库测试使用仓库 schema/reference_data 初始化的独立临时库。

### 审核时间

规划从业务读取时点后 120 秒开始，为人工比较预留审核缓冲。
每个候选返回 `review_deadline_at`：按各车辆首个新规划任务的到达时间，扣除从当前车辆位置到该任务的行驶时间，取最早的可批准截止时间。审批时会重新计算此截止时间（包括修复前已保存的候选），超过截止时间必须重新规划，避免到站时间尚未过期但已经来不及出发。
前端可以展示剩余审核时间；最终以服务端检查为准。
批准时若新规划站点的到达时间已早于实际审核时间，返回
`CANDIDATE_SCHEDULE_STALE`，不切换计划。重新生成时使用最新时点。
未调整的原计划站点不因这项时间校验被重新判定；其已有延误应继续在运营模块展示。
