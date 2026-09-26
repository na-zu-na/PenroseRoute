# PenroseRoute

P0 delivery operations backend.

## Backend setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## PostgreSQL setup

Create the database, all 14 P0 tables, and the P1 alert tables from the project root. The SQL files
do not create roles or contain credentials:

```powershell
psql -U postgres -d postgres -f database/create_database.sql
psql -U postgres -d penrose_route -f database/create_datatable.sql
```

Optionally load the reference/demo data:

```powershell
psql -U postgres -d penrose_route -f database/reference_data.sql
```

Copy `backend/.env.example` to `backend/.env` and set `DATABASE_URL` for the API:

```powershell
cd backend
copy .env.example .env
```

See [`database/README.md`](database/README.md) for the SQL file boundaries.

## Run tests

```powershell
.venv\Scripts\python.exe -m pytest -v
```

## Run the API

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Run the alert scanner (P1)

After bootstrapping a fresh database or applying the P1 alert SQL migration
to an existing P0 database, start **one managed worker process**
beside the API from `backend`:

```powershell
.venv\Scripts\python.exe -m app.jobs.alert_worker
```

Stop it with Ctrl+C (or stop the managed process), then restart the same command.
PostgreSQL ownership locking lets one worker scan at a time and a standby take
over after a stop or connection loss. FastAPI does not start a scanner. Set
`ALERT_SCAN_INTERVAL_SECONDS` to a positive integer in `backend/.env`; the
default 30-second scan cadence is not a delivery or notification SLA.

## Simulated vehicle positions for frontend maps

`GET /api/operations/simulated-positions?business_date=YYYY-MM-DD` returns GPS-style positions for every vehicle in the current plan. Poll it once per second; the response includes route paths, timestamps, motion states and `source: "SIMULATED"`. The default loop is 120 seconds. This is a read-only visualization feed; no real GPS is connected and business vehicle locations are unchanged. See [模拟车辆位置接口](backend/docs/SIMULATED_GPS.md) for the response contract, frontend usage and route-geometry limits.

## Agent 查询与异常恢复

`POST /api/agent/dispatch` 是正式只读入口，支持运营摘要、资源、Recovery 详情和按 Recovery ID 的 U01 比较。reader 与 dispatcher 均不能通过对话启动恢复、规划或审批。签名上下文支持缺参续问，默认规则与模板，可选 Bedrock。

正常规划使用 `POST /api/planning/generate`。异常恢复统一使用 `POST /api/incidents/{incident_id}/recovery`，由 `RECOVERY_ORCHESTRATION_MODE=deterministic|agent` 控制内部模式。候选必须经独立人工审批才能生效。

Agent 模式在 Candidate 持久化后才允许模型读取 U01 可信事实；模型只能返回事实 ID 的完整排列，失败回退模板。数据库事务结束后才调用模型。

U06 已提供周期 Worker 和正式提醒只读接口（`GET /api/operations/alerts`、`GET /api/operations/alerts/changes`）。当前 Agent 的提醒解释尚未接入这些查询，相关请求仍返回 `ALERT_QUERY_UNAVAILABLE`；不要用订单风险标志代替活动提醒。

最新配置、请求示例及调用时序见 [P1 Agent 只读 API](backend/docs/P1_AGENT_READONLY_API.md)。交付状态、测试和未完成依赖见 [P1 Agent Workstream](backend/docs/P1_AGENT_WORKSTREAM_STATUS.md)。旧 Dispatch/Recovery 文档保留原型设计，若存在差异以上述正式契约为准。
