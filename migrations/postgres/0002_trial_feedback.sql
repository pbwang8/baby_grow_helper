-- Phase 2.5 invited-family product feedback.

CREATE TABLE IF NOT EXISTS trial_feedback (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    child_id TEXT REFERENCES children(id) ON DELETE SET NULL,
    page TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    contact TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trial_feedback_family_created
    ON trial_feedback(family_id, created_at DESC);
