"""Tiny migration runner for Phase 2.5 database evolution.

This is deliberately smaller than Alembic while the schema is still young.
It gives us:

- Versioned SQL files under migrations/{sqlite,postgres}
- Idempotent `schema_migrations` tracking
- SQLite support for local/dev
- Postgres support via optional `psycopg`

ADR-0004 still names Alembic as the likely long-term migration tool. This
runner is the bridge that makes the Phase 2.5 service DB reproducible without
forcing an ORM rewrite in the same commit.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.core import db as sqlite_db

Backend = Literal["sqlite", "postgres"]

MIGRATIONS_ROOT = Path(__file__).resolve().parents[2] / "migrations"
DEFAULT_SQLITE_DB = "./data/babygrow.db"


class MigrationError(RuntimeError):
    """Migration failed or was misconfigured."""


@dataclass(frozen=True)
class MigrationFile:
    version: str
    path: Path


def detect_backend(database: str | None = None) -> Backend:
    """Infer backend from BGH_DATABASE_URL / database string."""
    value = database or os.environ.get("BGH_DATABASE_URL") or os.environ.get("BGH_DB", "")
    if value.startswith(("postgresql://", "postgres://")):
        return "postgres"
    return "sqlite"


def default_database_for_backend(backend: Backend) -> str:
    if backend == "postgres":
        url = os.environ.get("BGH_DATABASE_URL", "")
        if not url:
            raise MigrationError("BGH_DATABASE_URL is required for Postgres migrations")
        return url
    return os.environ.get("BGH_DB", DEFAULT_SQLITE_DB)


def list_migration_files(backend: Backend) -> list[MigrationFile]:
    directory = MIGRATIONS_ROOT / backend
    if not directory.exists():
        raise MigrationError(f"Migration directory not found: {directory}")
    out: list[MigrationFile] = []
    for path in sorted(directory.glob("*.sql")):
        version = path.stem.split("_", 1)[0]
        if not version:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        out.append(MigrationFile(version=version, path=path))
    return out


def apply_migrations(*, backend: Backend | None = None, database: str | None = None) -> list[str]:
    resolved_backend = backend or detect_backend(database)
    resolved_database = database or default_database_for_backend(resolved_backend)
    if resolved_backend == "sqlite":
        return _apply_sqlite(Path(resolved_database).expanduser().resolve())
    return _apply_postgres(resolved_database)


def _apply_sqlite(path: Path) -> list[str]:
    # Keep the legacy local schema path alive, then record SQL migrations on top.
    sqlite_db.init_db(path)
    conn = sqlite_db.get_conn(path)
    applied: list[str] = []
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        for mf in list_migration_files("sqlite"):
            exists = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (mf.version,)
            ).fetchone()
            if exists is not None:
                continue
            with sqlite_db.transactional(conn):
                _execute_sqlite_script(conn, mf.path.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)", (mf.version,)
                )
            applied.append(mf.version)
    finally:
        conn.close()
    return applied


def _execute_sqlite_script(conn: Any, script: str) -> None:
    """Execute a simple SQL migration script statement by statement.

    `sqlite3.Connection.executescript()` commits implicitly, which conflicts
    with our explicit transaction helper. The migration files in this repo are
    plain DDL statements, so semicolon splitting is sufficient here.
    """
    without_comments = "\n".join(
        line for line in script.splitlines() if not line.strip().startswith("--")
    )
    for stmt in without_comments.split(";"):
        sql = stmt.strip()
        if sql:
            conn.execute(sql)


def _apply_postgres(url: str) -> list[str]:
    try:
        psycopg: Any = importlib.import_module("psycopg")
    except ImportError as e:  # pragma: no cover - depends on optional package
        raise MigrationError(
            "Postgres migrations require psycopg. Install with "
            "`uv add 'psycopg[binary]'` or run in the deploy image."
        ) from e

    applied: list[str] = []
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            for mf in list_migration_files("postgres"):
                cur.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s",
                    (mf.version,),
                )
                if cur.fetchone() is not None:
                    continue
                cur.execute(mf.path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migrations(version) VALUES (%s)",
                    (mf.version,),
                )
                applied.append(mf.version)
        conn.commit()
    return applied


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BabyGrowHelper migration runner")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None)
    parser.add_argument("--database", help="SQLite path or Postgres URL")
    parser.add_argument("--apply", action="store_true", help="Apply pending migrations")
    parser.add_argument("--list", action="store_true", help="List migration files")
    args = parser.parse_args(argv)

    backend = args.backend or detect_backend(args.database)
    if args.list:
        for mf in list_migration_files(backend):
            print(f"{backend}:{mf.version}\t{mf.path.name}")
        return 0

    if args.apply:
        applied = apply_migrations(backend=backend, database=args.database)
        if applied:
            print("Applied migrations: " + ", ".join(applied))
        else:
            print("No pending migrations.")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
