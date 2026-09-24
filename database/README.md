# PostgreSQL database setup

`create_database.sql` is the single source of truth for database and schema
creation. It creates the database when needed, connects to it, installs the
required extensions, and creates all 14 P0 tables, constraints, indexes,
functions, and triggers. The application never runs it automatically.

Run the bootstrap from the project root with a PostgreSQL administrator role:

```powershell
psql -U postgres -d postgres -v database_name=penrose_route -f database/create_database.sql
```

After creation, copy `backend/.env.example` to `backend/.env` and set
`DATABASE_URL` to the same database:

```powershell
cd backend
copy .env.example .env
```

The bootstrap does not create roles or store passwords. Create the application
role separately according to the deployment environment's security policy.
The PostgreSQL administrator executing the script must have permission to
install `pgcrypto` and `btree_gist`.

The schema section is intended for initial creation of an empty database. It
does not drop or overwrite existing tables and is not a schema-upgrade tool.
The repository contains no database password; credentials must be supplied by
the operator through `DATABASE_URL` or the environment.
