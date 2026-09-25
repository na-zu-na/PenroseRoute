# Recovery Agent 接入说明

恢复 Agent 已由参考包升级为项目中的实际恢复链路。完整调度入口、部署配置及用法见 [DISPATCH_AGENT.md](DISPATCH_AGENT.md)。

## 分层

- `integrations/agent`：恢复 DTO、LangGraph、三种受控工具、Bedrock 事实排序及模板回退。没有数据库访问。
- `modules/incidents`：显式异常上报、确定性影响规则与范围升级。
- `modules/recovery/context.py`：防御校验 600 秒门槛、完成及交接标志。
- `modules/recovery/orchestration.py`：绑定已物化输入、求解器、独立 validator。
- `modules/recovery/evidence.py`：校验通过后计算全计划前后差异、改派、保持不变的订单和剩余风险，作为 Agent 的只读证据。
- `modules/recovery/workflow.py`：执行 attempt 链，只有 INFEASIBLE 扩围，ERROR/INVALID 停止。
- `modules/recovery/application.py`：默认 SQLAlchemy 事务适配器，创建 DRAFT、检查陈旧快照、保存完整候选。
- `modules/recovery/bootstrap.py`：默认适配器使用实际 OR-Tools；也保留自定义 adapter/solver/validator 的组合入口。
- `modules/decisions/service.py`：Approve / Reject / Modify；模型不能调用这些审批动作。

## 恢复生命周期

```text
事务 A：锁定当前计划及事件 → 物化快照 → DRAFT → 提交
事务外：受控 Agent → OR-Tools → 独立校验 → 事实解释
事务 B：复核当前版本及快照 → 保存失败 / 完整候选 → 提交
人工决策：Approve / Reject / Modify
```

候选只在 FEASIBLE + VALID 时产生。Completed stops 保留；已取货的故障车辆订单通过 HANDOVER 接手。范围外路线和订单成员复制到完整候选版本；Agent 不覆盖当前版本。

使用“候选方案”不表示已经执行。批准时复核基础版本、快照及时间，随后原 CURRENT 转为 SUPERSEDED，候选转为 CURRENT，异常变为 RESOLVED。拒绝取消候选；Modify 只建立下一固定范围尝试。

## API 与扩展

`POST /api/incidents/{id}/recovery`，body 为 `{}`。禁止客户端传入 prompt、scope 或路线。当前默认已经组装真实事务适配器和 OR-Tools，不再需要手动注入测试夹具；仍需配置 PostgreSQL、认证以及实际业务数据。

需要替换现有实现时，可设置 `app.state.recovery_workflow`，或使用：

```python
from app.modules.recovery.bootstrap import create_recovery_workflow

workflow = create_recovery_workflow(application, solver, validator, settings)
```

- `application.prepare_attempt(...)` 必须在返回前关闭所有事务，不能携带 ORM 懒加载对象。
- `application.finish_attempt(...)` 必须重新检查当前版本、快照和并发状态，事务内保存完整候选。
- `solver(serialized_input)` 返回 SolverResult；`validator(serialized_input, result)` 返回 ValidationReport。
- 默认 SQL 适配器已注入 `evidence_projector`。自定义适配器需提供 `ValidationReport.recovery_evidence`，或给 `create_recovery_workflow(..., evidence_projector=...)` 传入投影函数；未提供时明确标记全计划证据不可用。
- Agent 只排序已验证事实 ID；无模型时模板输出，调用失败时回退模板。地理估算来源是不可省略的解释事实。

新增结构化响应、认证/超时配置和任务说明逐项核对见 [Recovery Agent 功能验收](RECOVERY_AGENT_REQUIREMENTS.md)。

## 当前实现边界

默认使用地理距离和固定车速估算，不代表道路导航或实时交通。求解规模、人工审核时间、接口、测试与 PostgreSQL 验证限制均以 [调度 Agent 使用说明](DISPATCH_AGENT.md) 为准。

测试夹具位于 tests/agent，仅用于验证边界。新的 tests/dispatch 额外运行真实 OR-Tools、隔离 SQLite ORM 事务和 HTTP 端到端流程。SQLite 不验证 PostgreSQL 专有约束、触发器或并发锁；没有对用户数据库写入测试业务数据。
