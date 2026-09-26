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

## Recovery Agent

正常规划独立调用 `POST /api/planning/generate`，不经过 Agent。正式异常恢复入口只有 `POST /api/incidents/{incident_id}/recovery`；默认使用确定性编排，配置 `RECOVERY_ORCHESTRATION_MODE=agent` 后在同一接口内部启用 Recovery Agent，不新增前端 Agent 接口。

已提供实际 OR-Tools 求解、异常影响规则、恢复候选入库与独立人工审核接口。默认行程为地理距离估算，结果不会自动生效。P1 风险提醒的常驻扫描进程需按上文单独启动。

商家延迟可通过 `POST /api/incidents/merchant-delay` 评估。恢复生成的 Candidate 仍须调度员通过独立接口审核，不会自动生效。`/api/agent/dispatch` 是尚未挂载的调度 Agent 原型，`/deterministic-recovery` 不是正式接口。

调度 Agent 原型的配置与示例见 [调度 Agent 使用说明](backend/docs/DISPATCH_AGENT.md)；正式恢复编排设计见 [恢复 Agent 接入说明](backend/docs/AGENT_INTEGRATION.md)。

依据《Recovery Agent 开发任务说明》的逐项核对、本次补全和结构化结果契约见 [Recovery Agent 功能验收](backend/docs/RECOVERY_AGENT_REQUIREMENTS.md)。
