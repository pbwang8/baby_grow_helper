"""SQLite + sqlite-vec bootstrap for BabyGrowHelper.

Phase 0 scope (per prd/phase0-skeleton.md §2.1):
  - Tables: children, events, event_embeddings, usage_log
  - Three primitives: init_db(), get_conn(), transactional()
  - sqlite-vec is loaded but Phase 0 does not write embeddings.

Phase 1 additions (per prd/phase1-signals.md §2.1#1, decisions/0002):
  - Table: signals (the aggregation layer above raw events)
  - Strategy: append-to-SCHEMA_SQL + CREATE IF NOT EXISTS, no migration
    framework yet. Pre-MVP, no real user data to preserve.

Phase 2 additions (per prd/phase2-weekly-insight.md §4):
  - Table: weekly_insights (the cloud insight outputs)
  - Table: insight_feedback (multi-dim feedback per section, no 采纳率)
  - Same append-to-SCHEMA_SQL strategy. UUID PK + (child_id, week_start,
    version) unique index supports re-generation without seq collisions
    (PRD §3.5 — Cowork裁定).

Phase 2.5 additions (per prd/phase2_5-family-mobile-mvp.md §3):
  - Tables: users, families, family_members
  - Nullable family/user columns that let the local SQLite dev path stay
    backward-compatible while enabling a family-scoped API mode.
  - Table: trial_feedback (family-scoped product feedback during invite tests)

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
    family_id       TEXT REFERENCES families(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    birthday        TEXT NOT NULL,             -- ISO date YYYY-MM-DD
    profile_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_children_family
    ON children(family_id, id);

-- Phase 2.5: minimal family access-control foundation. SQLite remains the
-- local/dev database; the cloud version will mirror this shape in Postgres
-- via migrations (ADR-0004). access_code_hash stores a SHA-256 digest; raw
-- family codes never hit disk.
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS families (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    access_code_hash    TEXT NOT NULL UNIQUE,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS family_members (
    family_id       TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'member',
        -- owner | caregiver | viewer | member
    display_name    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (family_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_family_members_user
    ON family_members(user_id);

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
    user_id         TEXT REFERENCES users(id) ON DELETE SET NULL,
    family_id       TEXT REFERENCES families(id) ON DELETE SET NULL,
    backend         TEXT NOT NULL,             -- local | cloud
    model           TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    purpose         TEXT NOT NULL              -- recorder | signal | insight | ...
);

CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_log(ts);

-- Phase 1: signals (aggregated patterns, not raw events). Per decisions/0002,
-- we append-and-CREATE-IF-NOT-EXISTS rather than introduce a migration tool;
-- pre-MVP single-user, no live data to preserve. PRD: phase1-signals §2.1#1.
CREATE TABLE IF NOT EXISTS signals (
    id                       TEXT PRIMARY KEY,
    child_id                 TEXT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    signal_type              TEXT NOT NULL,
        -- interest_pattern | emotion_pattern | skill_pattern | anomaly | growth_leap
    domains_json             TEXT NOT NULL DEFAULT '[]',
    intensity                REAL NOT NULL,             -- 0.0-1.0
    child_age_months         INTEGER NOT NULL,          -- frozen at signal birth
    delta_from_last_period   REAL,                      -- nullable: prior window sparse
    confidence               REAL NOT NULL,             -- 0.0-1.0
    first_seen_at            TEXT NOT NULL,             -- ISO 8601
    last_seen_at             TEXT NOT NULL,             -- ISO 8601
    evidence_event_ids_json  TEXT NOT NULL,             -- JSON array of event ids, length >= 2
    status                   TEXT NOT NULL DEFAULT 'active',
        -- active | dormant | dismissed
    notes                    TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_child_first_seen
    ON signals(child_id, first_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_child_status
    ON signals(child_id, status);

-- Phase 2: weekly insights (cloud writer output) + per-section feedback.
-- Per decisions/0002: append-to-SCHEMA_SQL. PRD §3.5: UUID4 PK + version
-- column so re-generating the same week (e.g. after a prompt tweak) works
-- without sequence collisions. Business-uniqueness: (child_id, week_start,
-- version). PRD §4.1 lists the column shapes; we mirror them here.
CREATE TABLE IF NOT EXISTS weekly_insights (
    id                       TEXT PRIMARY KEY,            -- UUID4
    child_id                 TEXT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    week_start               TEXT NOT NULL,               -- ISO date (Mon, local tz)
    week_end                 TEXT NOT NULL,               -- ISO date (next Mon, exclusive)
    version                  INTEGER NOT NULL DEFAULT 1,  -- +1 on regenerate
    child_age_months         INTEGER NOT NULL,            -- frozen at write time
    sections_json            TEXT NOT NULL,               -- list[InsightSection]
    open_questions_json      TEXT NOT NULL,               -- list[str]
    sources_used_json        TEXT NOT NULL,               -- list of signal/event ids
    backend                  TEXT NOT NULL,               -- claude | local-fallback | remote-local
    model_used               TEXT NOT NULL,
    tokens_in                INTEGER NOT NULL,
    tokens_out               INTEGER NOT NULL,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_insights_child_week_ver
    ON weekly_insights(child_id, week_start, version);

CREATE INDEX IF NOT EXISTS idx_weekly_insights_child_created
    ON weekly_insights(child_id, created_at DESC);

-- PRD §3.6: feedback locates to section level, not paragraph anchor.
-- accuracy/value are nullable (parent may submit only one dimension).
-- ON DELETE CASCADE keeps the table tidy when a regenerated insight
-- supersedes the old one and the operator chooses to drop it.
CREATE TABLE IF NOT EXISTS insight_feedback (
    id                       TEXT PRIMARY KEY,            -- UUID4
    insight_id               TEXT NOT NULL REFERENCES weekly_insights(id) ON DELETE CASCADE,
    section_idx              INTEGER NOT NULL,            -- 0-based
    accuracy                 TEXT,                        -- accurate | inaccurate | unsure | NULL
    value                    TEXT,                        -- inspiring | unhelpful | missed_point | NULL
    free_text                TEXT,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_insight_feedback_insight
    ON insight_feedback(insight_id, section_idx);

-- Phase 2.5: lightweight product feedback for invited family trials. This is
-- separate from insight_feedback, which rates weekly insight sections.
CREATE TABLE IF NOT EXISTS trial_feedback (
    id                       TEXT PRIMARY KEY,
    family_id                TEXT REFERENCES families(id) ON DELETE CASCADE,
    child_id                 TEXT REFERENCES children(id) ON DELETE SET NULL,
    page                     TEXT NOT NULL,
    category                 TEXT NOT NULL,
        -- bug | idea | confusing | other
    message                  TEXT NOT NULL,
    contact                  TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_trial_feedback_family_created
    ON trial_feedback(family_id, created_at DESC);
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
        _ensure_backward_compatible_columns(conn)
    finally:
        conn.close()
    return target


def _ensure_backward_compatible_columns(conn: sqlite3.Connection) -> None:
    """Patch pre-Phase-2.5 SQLite files in place.

    `CREATE TABLE IF NOT EXISTS` will not add columns to an existing table.
    This keeps old local DBs usable without introducing a full migration
    framework before the Postgres track lands.
    """
    _ensure_column(
        conn,
        table="children",
        column="family_id",
        ddl="ALTER TABLE children ADD COLUMN family_id TEXT REFERENCES families(id) ON DELETE CASCADE",
    )
    _ensure_column(
        conn,
        table="usage_log",
        column="user_id",
        ddl="ALTER TABLE usage_log ADD COLUMN user_id TEXT REFERENCES users(id) ON DELETE SET NULL",
    )
    _ensure_column(
        conn,
        table="usage_log",
        column="family_id",
        ddl="ALTER TABLE usage_log ADD COLUMN family_id TEXT REFERENCES families(id) ON DELETE SET NULL",
    )


def _ensure_column(
    conn: sqlite3.Connection, *, table: str, column: str, ddl: str
) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {r["name"] for r in rows}:
        conn.execute(ddl)


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
