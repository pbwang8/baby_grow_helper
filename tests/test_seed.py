"""seed.ensure_yaoyao() must be idempotent and write the right row."""

from __future__ import annotations

from pathlib import Path

from src.core import db as db_module
from src.core.seed import YAOYAO_BIRTHDAY, YAOYAO_ID, YAOYAO_NAME, ensure_yaoyao


def test_seed_creates_yaoyao(tmp_db: Path) -> None:
    msg = ensure_yaoyao()
    assert "created" in msg

    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT id, name, birthday FROM children WHERE id = ?", (YAOYAO_ID,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["name"] == YAOYAO_NAME
    assert row["birthday"] == YAOYAO_BIRTHDAY


def test_seed_is_idempotent(tmp_db: Path) -> None:
    ensure_yaoyao()
    msg = ensure_yaoyao()
    assert "already exists" in msg

    conn = db_module.get_conn(tmp_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM children WHERE id = ?", (YAOYAO_ID,)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert count == 1
