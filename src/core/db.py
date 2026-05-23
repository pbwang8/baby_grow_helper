"""SQLite + sqlite-vec bootstrap for BabyGrowHelper.

Phase 0 scope (per prd/phase0-skeleton.md §2.1):
  - Tables: children, events, event_embeddings, usage_log
  - Three primitives: init_db(), get_conn(), transactional()
  - sqlite-vec is loaded but Phase 0 does not write embeddings.

The DB path is taken from BGH_DB env var (default ./data/babygrow.db) so
tests can point it at a tmpdir.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import sqlite_vec

DEFAULT_DB_PATH: Final[str] = "./data/babygrow.db"


def db_path() -> Path:
    return Path(os.environ.get("BGH_DB", DEFAULT_DB_PATH)).expanduser().resolve()


SCHEMA_SQL: Final[str] = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS children (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    birthday        TEXT NOT NULL,             -- ISO date YYYY-MM-DD
    profile_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    child_id        TEXT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    timestamp       TEXT NOT NULL,             -- ISO8601 with offset
    raw_text        TEXT NOT NULL,
    summary         TEXT NOT NULL,
    type            TEXT NOT NULL,             -- milestone | observation | routine | concern | other
    domains_json    TEXT NOT NULL DEFAULT '[]',
    emotions_json   TEXT NOT NULL DEFAULT '[]',
    context         TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    model_used      TEXT,                      -- which LLM produced the structuring
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_events_child_ts
    ON events(child_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS event_embeddings (
    event_id    TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    vector      BLOB,                          -- populated in Phase 1
    model       TEXT
);

CREATE TABLE IF NOT EXISTS usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    backend         TEXT NOT NULL,             -- local | cloud
    model           TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    purpose         TEXT NOT NULL              -- recorder | signal | insight | ...
);

CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);
"""


def get_conn(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with sqlite-vec loaded and row factory set."""
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | None = None) -> Path:
    """Create the DB file (if missing) and apply the schema. Idempotent."""
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn(target)
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()
    return target


@contextmanager
def transactional(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Wrap a block in BEGIN ... COMMIT/ROLLBACK.

    We run the connection with isolation_level=None (autocommit) so we can
    drive transactions explicitly here; that keeps semantics obvious and
    lets sqlite-vec extension calls work cleanly.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BabyGrowHelper DB tool")
    parser.add_argument("--init", action="store_true", help="Initialize schema")
    args = parser.parse_args(argv)
    if args.init:
        path = init_db()
        print(f"✓ DB initialized at {path}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
