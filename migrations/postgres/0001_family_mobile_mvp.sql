-- Phase 2.5 initial Postgres schema for family mobile MVP.
--
-- This is the service-side schema. It is intentionally stricter than the
-- local SQLite dev schema: family_id is NOT NULL on user data tables because
-- real family data must always be scoped.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS families (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    access_code_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS family_members (
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member',
    display_name TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (family_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_family_members_user
    ON family_members(user_id);

CREATE TABLE IF NOT EXISTS children (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    birthday TEXT NOT NULL,
    profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_children_family
    ON children(family_id, id);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    child_id TEXT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    type TEXT NOT NULL,
    domains_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    emotions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    context TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    model_used TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_events_family_child_ts
    ON events(family_id, child_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS event_embeddings (
    event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    vector vector(512),
    model TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_embeddings_family
    ON event_embeddings(family_id);

CREATE TABLE IF NOT EXISTS usage_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    family_id TEXT REFERENCES families(id) ON DELETE SET NULL,
    backend TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    purpose TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_family_ts
    ON usage_log(family_id, ts DESC);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    child_id TEXT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    signal_type TEXT NOT NULL,
    domains_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    intensity DOUBLE PRECISION NOT NULL,
    child_age_months INTEGER NOT NULL,
    delta_from_last_period DOUBLE PRECISION,
    confidence DOUBLE PRECISION NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    evidence_event_ids_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signals_family_child_first_seen
    ON signals(family_id, child_id, first_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_family_child_status
    ON signals(family_id, child_id, status);

CREATE TABLE IF NOT EXISTS weekly_insights (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    child_id TEXT NOT NULL REFERENCES children(id) ON DELETE CASCADE,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    child_age_months INTEGER NOT NULL,
    sections_json JSONB NOT NULL,
    open_questions_json JSONB NOT NULL,
    sources_used_json JSONB NOT NULL,
    backend TEXT NOT NULL,
    model_used TEXT NOT NULL,
    tokens_in INTEGER NOT NULL,
    tokens_out INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_insights_family_child_week_ver
    ON weekly_insights(family_id, child_id, week_start, version);

CREATE INDEX IF NOT EXISTS idx_weekly_insights_family_child_created
    ON weekly_insights(family_id, child_id, created_at DESC);

CREATE TABLE IF NOT EXISTS insight_feedback (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    insight_id TEXT NOT NULL REFERENCES weekly_insights(id) ON DELETE CASCADE,
    section_idx INTEGER NOT NULL,
    accuracy TEXT,
    value TEXT,
    free_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_insight_feedback_family_insight
    ON insight_feedback(family_id, insight_id, section_idx);
