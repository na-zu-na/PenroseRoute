# PenroseRoute

P0 delivery operations backend.

## Backend setup

```powershell
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## PostgreSQL setup

Create the database and all 14 P0 tables from the project root. The SQL file
does not create roles or contain credentials:

```powershell
psql -U postgres -d postgres -v database_name=penrose_route -f database/create_database.sql
```

The command creates the database when needed, connects to it, installs the
required PostgreSQL extensions, and creates the complete schema. Copy
`backend/.env.example` to `backend/.env` and set `DATABASE_URL` for the API:

```powershell
cd backend
copy .env.example .env
```

[`database/create_database.sql`](database/create_database.sql) is the only
source of truth for database and table creation. See
[`database/README.md`](database/README.md) for details.

## Run tests

```powershell
.venv\Scripts\python.exe -m pytest -v
```

## Run the API

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```
