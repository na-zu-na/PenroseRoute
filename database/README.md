# PostgreSQL database setup

Database initialization is split into three explicit SQL files:

- `create_database.sql` creates the `penrose_route` database.
- `create_datatable.sql` creates the 14 P0 tables, the two P1 alert tables,
  the migration ledger, and their supporting database objects.
- `reference_data.sql` optionally loads the reference/demo data, including
  one active alert and its creation event. This is the fixed September 25
  legacy scenario, not the server seed.
- `reference_data_future_week.sql` loads fresh planning resources and orders
  for the next seven Singapore business dates at import time.

The application never runs these scripts automatically.

Run the bootstrap from the project root with a PostgreSQL administrator role:

```powershell
psql -U postgres -d postgres -f database/create_database.sql
psql -U postgres -d penrose_route -f database/create_datatable.sql
psql -U postgres -d penrose_route -f database/reference_data.sql
```

After creation, copy `backend/.env.example` to `backend/.env` and set
`DATABASE_URL` to the same database:

```powershell
cd backend
copy .env.example .env
```

The bootstrap does not create roles or store passwords. Create the application
role separately according to the deployment environment's security policy.
The PostgreSQL administrator executing `create_datatable.sql` must have
permission to install `pgcrypto` and `btree_gist`. The reference-data step is
optional.

The scripts are intended for initial creation of an empty database. They do
not drop or overwrite existing tables and are not schema-upgrade tools.
The bootstrap records V001 as applied. Upgrade an existing P0 database with
`migrations/V001__p1_risk_alerts.sql` via `apply_migrations.py` instead of
rerunning the bootstrap; do not apply V001 again after a fresh bootstrap.
The repository contains no database password; credentials must be supplied by
the operator through `DATABASE_URL` or the environment.

## Server initialization: next seven business dates

On a **new empty database**, run the schema script and the future-week seed
instead of the fixed `reference_data.sql` demo seed:

```powershell
psql -v ON_ERROR_STOP=1 -U postgres -d penrose_route -f database/create_datatable.sql
psql -v ON_ERROR_STOP=1 -U postgres -d penrose_route -f database/reference_data_future_week.sql
psql -v ON_ERROR_STOP=1 -U postgres -d penrose_route -f database/validate_future_week_seed.sql
```

Run `create_database.sql` first if the database does not yet exist. The seed
uses the Singapore calendar date when imported: tomorrow through day +7,
12 `PLANNED` orders and five `PLANNED` vehicle-driver pairs per day (84 orders,
35 daily pairs total). Merchant pickup, customer delivery, windows and all
timestamps are populated for each date. Running it again or on a populated
database fails before changing existing data.

This seed intentionally contains **no** Delivery Plans, Routes, Stops,
Incidents, Recovery Attempts or alerts. Generate and confirm a plan for a
selected business date through the normal Planning API/UI after deployment;
create incidents through the formal workflows. The seed cannot be used to
refresh future dates in an already operating database.

## Rebuild the September 27 demo without touching the live database

From `backend/`, with `.env` still pointing to `penrose_route` and the local
Singapore OSRM service running:

```powershell
.venv\Scripts\python.exe tools/prepare_rebuilt_demo.py
.venv\Scripts\python.exe tools/rebuild_demo_scenario.py
.venv\Scripts\python.exe tools/validate_rebuilt_demo.py
```

The first command creates the separate `penrose_route_demo_rebuilt` database
from the checked-in schema and seed SQL. It removes legacy hand-authored plan
and incident outcomes **only in that new database**. The second command uses
the formal HTTP API, OSRM, and OR-Tools to create a real September 27 plan,
execute stops, record a vehicle breakdown, and generate a recovery candidate.
It first records two short, resolved merchant-delay incidents (5 and exactly
10 minutes, one per merchant) against the Current Plan, so the September 27
Incident list contains three genuine workflow-generated records.
The candidate stays `CANDIDATE`; the base plan stays `CURRENT` until a dispatcher
approves it. The third command checks membership, route geometry and Stop
alignment, distances, durations, and handover/frozen-order impacts without
writing to the database.

If the staging database already exists, the preparation command refuses to
overwrite it. To intentionally start the **staging database only** over, use
`tools/prepare_rebuilt_demo.py --replace-staging` before rerunning the other
commands. The original `penrose_route` database remains the rollback source.
To show the rebuilt data, run
`.venv\Scripts\python.exe tools/switch_demo_database.py rebuilt` and restart
the backend. Run the same command with `original` to return to `penrose_route`.
This changes only the database name in the ignored `backend/.env`; it never
prints the credentials or drops either database. Switch to `original` before
using the preparation command again.

Some older API/integration tests assume the hand-authored September 25 plan,
incident, and fixed UUIDs from `reference_data.sql`. They are not acceptance
tests for this rebuilt database, which intentionally replaces those derived
records. Run the read-only validator above for the rebuilt scenario; run those
legacy fixed-seed tests against a separately initialized reference database.
