# PenroseRoute

P0 delivery operations backend.

## Backend setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## PostgreSQL setup

Create the database and all 14 P0 tables from the project root. The SQL files
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

## Dispatch Agent

跨模块调度入口为 `POST /api/agent/dispatch`：支持资源/运营查询、异常恢复、候选读取、版本比较和多轮参数补充。正常规划独立调用 `POST /api/planning/generate`，不经过 Agent。默认规则识别，可选 Bedrock；API 需要配置身份令牌及上下文签名密钥。

已提供实际 OR-Tools 求解、异常影响规则、恢复候选入库与独立人工审核接口。默认行程为地理距离估算，结果不会自动生效。没有前端地图或常驻监控进程。

完整配置、请求示例、业务 API 和测试边界见 [调度 Agent 使用说明](backend/docs/DISPATCH_AGENT.md)。原有恢复编排设计见 [恢复 Agent 接入说明](backend/docs/AGENT_INTEGRATION.md)。

依据《Recovery Agent 开发任务说明》的逐项核对、本次补全和结构化结果契约见 [Recovery Agent 功能验收](backend/docs/RECOVERY_AGENT_REQUIREMENTS.md)。
