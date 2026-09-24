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
