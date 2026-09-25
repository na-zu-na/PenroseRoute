# 跨模块调度 Agent 使用说明

## 当前可以运行的流程

本次在现有 P0 恢复 Agent 上增加了调度入口，并补齐正常规划、异常上报、恢复入库和人工决策的后端实现。没有新增数据库表，继续使用现有 14 张表。

```text
自然语言请求 + 可选上下文
  → 规则意图识别 / 可选 Bedrock 结构化意图识别
  → 参数补充、角色校验、白名单校验
  → 资源查询 / 运营查询 / 异常恢复 / 候选查询 / 版本比较
  → 事实摘要 + 每一步工具结果 + 签名上下文

异常恢复：准备 DRAFT → OR-Tools → 独立校验 → CANDIDATE + PENDING_REVIEW
人工审核：Approve / Reject / Modify
```

调度 Agent 可以识别并串联多个动作；内部恢复 Agent 保持固定 LangGraph。模型不生成订单参数、SQL、路线、范围、审批或任意工具代码。正常规划只通过 `POST /api/planning/generate` 的固定业务流程调用 OR-Tools；不经过 Agent，也不在意图工具白名单中。

## 启动与配置

在 backend 目录安装并启动：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m uvicorn app.main:app --reload
```

数据库使用项目原有 PostgreSQL 建表脚本及 DATABASE_URL，初始化方法见根 README。程序不会自动建库、清空表或灌入示例数据。

在自己的 `.env` 配置：

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/penrose_route
BUSINESS_TIMEZONE=Asia/Singapore
DISPATCH_INTENT_PROVIDER=rules
AGENT_EXPLANATION_PROVIDER=template
DISPATCH_CONTEXT_SECRET=替换成至少32字符的随机字符串
DISPATCH_API_TOKENS='{"替换成至少32字符的独立随机令牌":{"subject":"dispatcher-1","role":"dispatcher"}}'
```

上述密钥占位符必须替换。可运行 `python -c 'import secrets; print(secrets.token_urlsafe(32))'` 分别生成两个不同的随机值。不要提交 `.env`。

- `dispatcher`：可以查询、生成计划、上报异常，并通过独立人工接口审核。
- `reader`：只能查询，无法生成或审核。
- 当前凭据适用于**一个业务工作区**，不是多租户权限系统。以后可替换 `authenticate_dispatch_user` 接入 SSO。
- 不配置凭据时返回 503，不会默认开放接口。
- Bedrock 可选：设置 `DISPATCH_INTENT_PROVIDER=bedrock`、`BEDROCK_MODEL_ID`、`AWS_REGION`。恢复事实解释另由 `AGENT_EXPLANATION_PROVIDER` 控制。模型必须支持 Converse tool use；AWS 凭据使用标准凭据链。

## 调度入口

`POST /api/agent/dispatch`

请求头：`Authorization: Bearer <你的令牌>`。

```json
{
  "message": "看看今天哪些订单有迟到风险，再看看空闲车辆",
  "context": {},
  "context_token": null
}
```

支持的示例：

- “查看 2026-09-25 的配送风险和车辆资源”
- “生成恢复方案”，配合 `context.incident_id`
- “生成恢复方案并比较差异”，配合 `context.incident_id`
- “查看候选方案”，配合 `context.recovery_plan_id`
- “比较计划差异”，配合 `context.base_plan_id` 和 `context.candidate_plan_id`

“生成今天配送计划”会返回独立规划接口的使用提示，不调用意图模型或规划服务。

可用上下文字段只有 `business_date`、`incident_id`、`recovery_plan_id`、`base_plan_id`、`candidate_plan_id`。UUID 可由前端选择器传入，也可用“事件: UUID”“原计划: UUID”等明确标签写在文本中。不猜测车辆名称对应的 ID，也不自动从多个异常中挑选一个。

业务响应顶层遵循原 envelope，`data` 包含：

| 字段 | 含义 |
| --- | --- |
| status | COMPLETED / NEEDS_INPUT / FAILED |
| message | 根据业务结果生成的摘要或补充信息提示 |
| observations | 按执行顺序返回工具名、成功状态、业务码与结果 |
| context | 当前选择的日期和事件/方案 ID |
| context_token | 30 分钟有效、绑定用户的签名上下文 |
| planner_source | rules / model / fallback |

调度执行结果使用 HTTP 200 表示成功接收任务，工具失败时顶层 `success=false`、`code=DISPATCH_FAILED`；调用方必须读取这些业务字段。认证和请求格式错误仍使用 401/403/422，配置缺失使用 503。

规则模式覆盖文档中的表达，不是通用中文语义理解。Bedrock 可扩展查询表达，但生成动作仍要求服务端识别明确的“生成/启动”等执行意图。否定、条件和审批表达不会直接触发写操作。

## 多轮补充与串联

第一次发送“查看空闲车辆”但未提供日期，得到 NEEDS_INPUT。第二次提交返回的 context_token，message 写“今天”或“2026-09-25”，即可继续未完成查询。

第一次发送“生成恢复方案并比较差异”但缺 incident_id，第二次可用相同 token 提交“事件: UUID”，或 message 为“继续”并在 context 填 incident_id。

执行顺序由服务端整理为查询 → 生成 → 候选读取 → 比较，恢复后比较会自动增加候选查询以取得权威 base/candidate ID。每种动作最多一次；异常停止后续动作，已完成结果仍返回。发生失败不自动重试写操作。

上下文只保存选定对象和未执行动作，不保存任意聊天记录。签名提供防篡改，不提供加密；不要把 token 放到日志或公共链接中。换日期会清除旧事件/方案选择，新的显式选择会保留。角色权限每次请求重新校验。

## 业务 API

| 方法与路径 | 用途 |
| --- | --- |
| GET `/api/operations?business_date=YYYY-MM-DD` | 配送进度、风险及未解决异常 |
| GET `/api/resources/availability?business_date=YYYY-MM-DD` | 有效车人绑定、资源状态及空闲数量 |
| POST `/api/planning/generate` | `{"business_date":"YYYY-MM-DD"}`，保存初始 DRAFT |
| POST `/api/delivery-plans/{id}/activate` | 人工确认初始 DRAFT 生效，无请求体 |
| POST `/api/delivery-plans/{id}/cancel` | 取消初始 DRAFT，无请求体 |
| POST `/api/incidents` | 显式上报车辆不可用或商家延迟 |
| POST `/api/incidents/{id}/recovery` | `{}`，运行异常恢复 |
| GET `/api/recovery-plans/{id}` | 查询恢复尝试及候选摘要 |
| GET `/api/delivery-plans/compare?base_plan_id=UUID&candidate_plan_id=UUID` | 比较同一计划组的两个版本 |
| POST `/api/recovery-plans/{id}/approve` | `{"reason":"确认接手"}` |
| POST `/api/recovery-plans/{id}/reject` | `{"reason":"需要重新评估"}` |
| POST `/api/recovery-plans/{id}/modify` | `{"reason":"扩大范围"}`，只进入下一确定性范围 |

车辆异常示例：

```json
{
  "business_date": "2026-09-25",
  "incident_type": "VEHICLE_UNAVAILABLE",
  "vehicle_route_id": "当前计划中的路线UUID",
  "incident_location_id": "已确认事故地点的UUID"
}
```

商家延迟示例：

```json
{
  "business_date": "2026-09-25",
  "incident_type": "MERCHANT_DELAY",
  "merchant_id": "商家UUID",
  "updated_ready_at": "2026-09-25T12:30:00+08:00"
}
```

IncidentService 根据订单执行状态生成影响记录。车辆不可用时，未取货订单重新取货，已取货未完成订单需要 HANDOVER，已完成订单冻结。商家延迟不超过 600 秒时更新备货时间、保守传播下游等待并评估风险，事件直接解决；超过门槛才进入恢复。多订单备货时间仍逐订单更新。

## 求解与落库边界

- OR-Tools 处理取送顺序、硬时间窗、容量、可用车人绑定、已取货任务的车载初始负载和允许车辆集合；独立 validator 重算约束及指标。
- 每次求解最多 5 秒，当前实现限 100 个订单、100 组车人资源。超时/未知搜索失败为 ERROR，只有明确无解才扩围。
- AFFECTED_ROUTE 限受影响路线原车辆；CROSS_ROUTE 加入不占用其他保留路线的资源；ALL_REMAINING 允许重排各路线未完成任务，已取货且无需交接的订单仍绑定原车辆。未分配的既有订单目前保持原未分配记录。
- 已完成 stops 和未受影响路线会复制到完整候选版本；正在服务、完成记录不是连续前缀或影响快照过期时停止并提示人工核对。
- 准备 DRAFT 和最终保存采用两个短事务。外部模型与求解器调用都在事务之外。保存及审批检查当前基础计划和数据快照。
- Modify 原子取消旧候选、记录决定并建立下一 DRAFT，提交后执行求解；范围耗尽不修改原决定。
- PostgreSQL 使用按业务日期 advisory lock，并对相关实体加锁。当前锁粒度偏粗，适合小规模原型，尚未做高并发负载验证。
- 不承诺跨网络重试的 exactly-once；依靠状态、attempt 链和既有数据库约束避免重复生效，没有新增通用幂等表。

## 当前精度和范围

**默认使用球面距离和固定速度 8.33 m/s 估算行程，结果标记 GEOGRAPHIC_ESTIMATE。没有接入道路、堵车、真实导航或实时 GPS。** 路线几何也未生成。正常 PlanningService 支持注入矩阵 provider；恢复默认适配器仍使用内置估算矩阵，接入真实路网时需统一替换这一计算入口。

为留出人工审核时间，规划起点默认延后 300 秒。初始正常计划生成 DRAFT，经 `/activate` 确认才成为 CURRENT；这是本次扩展的显式确认流程，与原 P0 文档“正常规划直接创建 CURRENT”的行为不同。恢复候选始终需人工审核。计划时间过期或配送事实变化时会拒绝生效。

运营监控是按请求读取数据库及截止时间计算风险，没有常驻采集进程或自动故障上报。异常由显式事件接口上报。资源查询依据当前绑定与状态，不代表未来排班或实时剩余载重。

本次交付后端能力及 Swagger 接口，没有新增前端地图/聊天界面，也没有实现完整资源 CRUD、停靠点执行状态上报、订单导入或通知系统。现有数据库数据可由已有业务或 SQL 初始化流程提供。

## 验证与代码位置

```bash
python -m pytest tests/dispatch tests/agent tests/api tests/unit \
  tests/database/test_database_schema.py tests/database/test_database_bootstrap.py -q
```

已验证真实 OR-Tools 与 ORM/HTTP 组合：生成计划 → 人工启用 → 车辆异常上报 → 查询运营与资源 → 自动恢复 → 读取候选并比较 → 人工批准。还覆盖 600/601 秒、交接冻结、拒绝、Modify、范围耗尽、过期快照、签名上下文、角色权限和模型异常回退。

数据库测试使用独立 SQLite 表结构验证应用查询和事务逻辑，**不等同于验证 PostgreSQL 的触发器、部分索引和并发锁**。真实 PostgreSQL 回归应在隔离测试库中继续运行原有 integration 测试及部署验收。本次没有对用户数据库写入业务数据，也没有调用真实 Bedrock。

主要入口：

- `app/api/routes/dispatch.py`：调度 API。
- `app/integrations/dispatch_agent/`：意图 schema、规则与 Bedrock 实现。
- `app/modules/dispatch/`：跨模块执行、真实查询、签名上下文。
- `app/integrations/optimization/solver.py`：实际 OR-Tools 求解与独立校验。
- `app/modules/planning/service.py`：正常规划及初始计划确认。
- `app/modules/incidents/service.py`：异常上报及影响规则。
- `app/modules/recovery/application.py`：恢复事务、完整候选保存。
- `app/modules/decisions/service.py`：人工审核与版本切换。
