# P1 Agent 扩展交付状态

依据 P1_AGENT_EXTENSION_IMPLEMENTATION_PLAN，当前交付范围为 Recovery 解释契约、只读对话入口与运营摘要；不称为完整 P1。

## Task 1：精确事实排列 — 完成

共享校验位于 `app/integrations/agent/explanation.py`。模型必须返回全部事实 ID 的恰好一次排列；未知、重复、遗漏、空列表、错误格式及超时统一回退模板，记录 `AGENT_EXPLANATION_FALLBACK`。Graph 与只读摘要复用同一校验点。

## Task 2：持久化后的 U01 解释 — 完成

正式入口仍为 `POST /api/incidents/{incident_id}/recovery`，默认 deterministic。Agent 模式先用原固定 Graph 单次求解，再保存完整 Candidate、U01 证据和可审核模板。事务提交后可选模型才接收 U01 事实 ID；随后短事务复核 Attempt、Candidate、Base 和业务快照，只更新解释及来源。

解释保存失败保留已提交模板。并发审核或快照变化时不覆盖决定。模型不重复求解，不创建第二个 Candidate。U01 剩余指标不可计算仍返回 `NO_COMPARABLE_REMAINDER_SNAPSHOT`。

正式来源：`agent`=模型成功，`template`=未请求模型/持久化模板，`template_fallback`=请求模型后失败且回退成功保存，`deterministic`=确定性模式。模型成功但解释写入失败时，数据库和 HTTP 均保留模板，不虚报成功。

## Task 3：正式只读对话 — 完成

`POST /api/agent/dispatch` 已正式注册，reader 与 dispatcher 均只能读取。保留 response envelope、DispatchCommand 和绑定用户且会过期的签名上下文。

意图白名单：运营摘要、资源查询、Recovery 详情、按 Recovery ID 的 U01 比较，以及提醒解释请求。所有自然语言写请求仅提示已有业务 API。模型输出写意图无法通过 DTO 白名单；路由装配没有 Recovery 写能力。

读取业务服务后物化 JSON，关闭 Session 后才调用模型。正常业务数据库异常返回 FAILED；缺日期或实体返回 NEEDS_INPUT；无 Current Plan 不从其他日期或 Order 标志拼凑当前计划。

## Task 4：运营摘要完成；正式 Alert 解释等待非 Agent Task 7

已提供业务日期、Current Plan/版本、运营计算时点、未完成和 AT_RISK 订单数、未解决 Incident 数、待审核 Candidate 数、as_of、facts 和 truncated。

已支持 order_id/alert_id 选择和跨日期清除；缺实体时澄清。但当前仓库只有 U06 实体、Repository、确定性生命周期服务，尚无计划要求的 Task 7 活动/历史/游标查询服务。因此：

- 活动提醒数、原因计数和提醒数据返回 null，missing_reasons 包含 ALERT_QUERY_UNAVAILABLE。
- 回复明确“提醒数据不可用”，绝不查询 Order.risk_status 冒充持久化 Alert。
- Agent 不调用 AlertRepository、风险评估服务或扫描器，不新增提醒表、Migration 或 Worker。
- 尚未实现真实 Alert Snapshot 的按需解释；需 Task 7 正式 DTO/签名到位后接入，不引入虚构兼容层。

## Task 5：已运行部分与未交付依赖

真实 PostgreSQL、真实 OR-Tools、正式 HTTP/Graph/受控工具已参与回归。模型使用可控替身；真实 Bedrock 未验证。

完整验收在临时数据库 `penrose_agent_test_<随机 UUID>` 执行，初始化仓库 P0 schema/reference data，通过进程环境 DATABASE_URL 指向临时库，结束后删除。U06 migration/lifecycle 测试继续使用自己的 `penrose_p1_test_<UUID>` 临时库。SQLite 兼容测试仅验证应用逻辑，不代表 PostgreSQL 约束验证。

非 Agent Task 6 周期 Worker 和 Task 7 查询尚未交付，所以“无人查询产生提醒 → 对话解释 → 再扫描去重 → 历史状态解释”的联合验收未完成。已有 U06 生命周期测试不能替代该链路。

2026-09-26 全量回归：317 passed、0 failed、0 skipped；临时 PostgreSQL 库在测试结束后已删除。自动化用受控模型替身覆盖完整事实排列、遗漏/非法输出、超时、解释写入失败和并发审核回退；没有调用真实 Bedrock，真实模型成功次数为 0。
