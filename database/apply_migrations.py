"""Apply explicit, checksummed SQL upgrades to an explicitly named database."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

import psycopg
from sqlalchemy.engine import URL, make_url


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
VERSION_PATTERN = re.compile(r"^V(\d+)__[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    """The migration history or supplied scripts are unsafe to apply."""


def _scripts(directory: Path) -> list[tuple[int, str, Path, str]]:
    scripts = []
    versions = set()
    for path in directory.glob("V*.sql"):
        match = VERSION_PATTERN.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        number = int(match.group(1))
        if number in versions:
            raise MigrationError(f"Duplicate migration version: V{number:03d}")
        versions.add(number)
        scripts.append((number, f"V{number:03d}", path, hashlib.sha256(path.read_bytes()).hexdigest()))
    return sorted(scripts)


def apply_migrations(database_url: str | URL, *, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply missing versions atomically; never fall back to the API database setting."""
    url = make_url(database_url) if isinstance(database_url, str) else database_url
    if url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.database:
        raise MigrationError("An explicit PostgreSQL database URL is required")
    scripts = _scripts(migrations_dir)
    if not scripts:
        raise MigrationError("No migration scripts found")
    applied_now = []
    # Render as a libpq URI so operator-supplied TLS, timeout and routing
    # parameters remain in force instead of being silently discarded.
    conninfo = url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(conninfo) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(55120, 3)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS public.schema_migrations (
                    version varchar(16) PRIMARY KEY,
                    checksum char(64) NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            applied = dict(connection.execute(
                "SELECT version, checksum FROM public.schema_migrations"
            ).fetchall())
            available = {version for _, version, _, _ in scripts}
            if applied.keys() - available:
                raise MigrationError("Applied migration script is missing")
            highest_applied = max((int(version[1:]) for version in applied), default=0)
            for number, version, path, checksum in scripts:
                if version in applied:
                    if applied[version].strip() != checksum:
                        raise MigrationError(f"Migration checksum changed: {version}")
                    continue
                if number < highest_applied:
                    raise MigrationError(f"Migration out of order: {version}")
                connection.execute(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO public.schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
                applied_now.append(version)
    return applied_now


def main() -> int:
    database_url = os.environ.get("P1_MIGRATION_DATABASE_URL")
    if not database_url:
        print("P1_MIGRATION_DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        versions = apply_migrations(database_url)
    except (MigrationError, psycopg.Error, ValueError) as error:
        print(f"Migration failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print("Applied: " + (", ".join(versions) if versions else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
