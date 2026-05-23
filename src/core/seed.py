"""Seed real-user state.

Phase 0 only seeds child=yaoyao. Tests / fixtures use the synthetic child
"小明" instead (per decisions/0001 F16) — DO NOT add 小明 here.
"""

from __future__ import annotations

import sys

from src.core import db as db_module

YAOYAO_ID = "yaoyao"
YAOYAO_NAME = "瑶瑶"
YAOYAO_BIRTHDAY = "2023-11-01"


def ensure_yaoyao() -> str:
    """Idempotent: insert if missing, otherwise leave the existing row alone."""
    conn = db_module.get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM children WHERE id = ?", (YAOYAO_ID,)
        ).fetchone()
        if existing is not None:
            return f"child '{YAOYAO_ID}' already exists, skipping"
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            (YAOYAO_ID, YAOYAO_NAME, YAOYAO_BIRTHDAY),
        )
        return f"✓ created child '{YAOYAO_ID}' ({YAOYAO_NAME}, born {YAOYAO_BIRTHDAY})"
    finally:
        conn.close()


def main() -> int:  # pragma: no cover
    db_module.init_db()
    msg = ensure_yaoyao()
    print(msg)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
