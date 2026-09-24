# PostgreSQL database setup

Database initialization is split into three explicit SQL files:

- `create_database.sql` creates the `penrose_route` database.
- `create_datatable.sql` creates all 14 P0 tables, extensions, constraints,
  indexes, functions, and triggers.
- `reference_data.sql` optionally loads the reference/demo data.

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
The repository contains no database password; credentials must be supplied by
the operator through `DATABASE_URL` or the environment.
