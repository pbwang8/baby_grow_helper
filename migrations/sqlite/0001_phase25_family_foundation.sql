-- Phase 2.5 SQLite compatibility migration.
--
-- The actual local-dev schema is still maintained in src/core/db.py. This
-- migration records that the Phase 2.5 family foundation has been applied
-- after db.init_db() has patched older SQLite files in place.

CREATE INDEX IF NOT EXISTS idx_children_family
    ON children(family_id, id);

CREATE INDEX IF NOT EXISTS idx_family_members_user
    ON family_members(user_id);
