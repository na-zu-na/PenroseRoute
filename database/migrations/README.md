# P1 alert schema upgrade

`V001__p1_risk_alerts.sql` adds only `risk_alerts`, `risk_alert_changes`, and
their indexes/sequence. The runner creates `schema_migrations` for version and
SHA-256 tracking. It does not rebuild or seed the 14 P0 tables.
Fresh databases initialized with `create_datatable.sql` already include these
objects and a V001 ledger entry; use this migration only for existing P0 databases.

## Apply explicitly

1. Stop writers or arrange a maintenance window. Back up the existing database,
   for example with `pg_dump -Fc -f penrose_route_before_p1.dump penrose_route`.
   Verify the dump can be restored to a **separate** database before upgrading.
2. Set `P1_MIGRATION_DATABASE_URL` to the exact target PostgreSQL database URL
   in the operator's environment. Do not put credentials in source control.
3. Run `backend/.venv/Scripts/python database/apply_migrations.py` from the
   project root (on Unix, use the virtual environment's `bin/python`).
4. Confirm `V001` appears in `public.schema_migrations` and the existing P0
   data remains readable. A second run reports `Applied: none`.

The API does not run migrations at startup. The runner never falls back to
`DATABASE_URL` and never prints the connection URL. It uses one transaction and
one advisory upgrade lock; failed SQL or checksum/order validation rolls back
the attempted upgrade. Applied scripts are immutable: add a new version rather
than editing `V001`.

## Cursor-write protocol

All alert change writers use `AlertRepository.append_change` in their existing
write transaction. It takes transaction-level advisory lock `(55120, 1)` **before**
calling `nextval` on `risk_alert_change_cursor_seq` (`CACHE 1`), inserts the
change, and holds the lock through commit or rollback. The `change_id` column
has no automatic default. Sequence gaps after rollback are harmless; committed
changes remain readable in cursor order. Worker ownership later uses a
different lock `(55120, 2)` and does not replace this protocol.

## Recovery

This is a forward-only migration. It does not provide a lossless downgrade:
dropping alert tables would erase alert history. If application rollback is
needed, leave the additive tables in place and run the prior application build.
For database rollback, restore the verified pre-upgrade dump into a separate
database and switch the application only after validation; changes made since
the backup need an explicit reconciliation plan. Never drop P0 tables or seed
data to simulate rollback.
