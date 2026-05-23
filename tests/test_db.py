"""Schema bring-up, write/read roundtrip, transaction rollback."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core import db as db_module


def test_init_db_creates_all_tables(tmp_db: Path) -> None:
    conn = db_module.get_conn(tmp_db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    table_names = {r["name"] for r in rows}
    expected = {"children", "events", "event_embeddings", "usage_log"}
    assert expected.issubset(table_names), f"missing: {expected - table_names}"


def test_init_db_is_idempotent(tmp_db: Path) -> None:
    db_module.init_db(tmp_db)
    db_module.init_db(tmp_db)  # second call must not raise


def test_event_roundtrip(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        conn.execute(
            """
            INSERT INTO events
              (id, child_id, timestamp, raw_text, summary, type,
               domains_json, emotions_json, context, source, model_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_test_1",
                "xiaoming",
                "2026-05-19T10:00:00+08:00",
                "原文",
                "摘要",
                "observation",
                '["language"]',
                '["happy"]',
                "context",
                "manual",
                "qwen2.5:3b-instruct",
            ),
        )
        row = conn.execute("SELECT * FROM events WHERE id = ?", ("evt_test_1",)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["child_id"] == "xiaoming"
    assert row["summary"] == "摘要"


def test_foreign_key_enforced(tmp_db: Path) -> None:
    conn = db_module.get_conn(tmp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO events
                  (id, child_id, timestamp, raw_text, summary, type)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_orphan",
                    "ghost_child",
                    "2026-05-19T10:00:00+08:00",
                    "原文",
                    "摘要",
                    "observation",
                ),
            )
    finally:
        conn.close()


def test_transactional_rolls_back(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with db_module.transactional(conn):
                conn.execute(
                    """
                    INSERT INTO events
                      (id, child_id, timestamp, raw_text, summary, type,
                       domains_json, emotions_json, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "evt_rb",
                        "xiaoming",
                        "2026-05-19T10:00:00+08:00",
                        "原文",
                        "摘要",
                        "observation",
                        "[]",
                        "[]",
                        "manual",
                    ),
                )
                raise RuntimeError("boom")
        row = conn.execute("SELECT id FROM events WHERE id = ?", ("evt_rb",)).fetchone()
        assert row is None, "rolled-back row must not be persisted"
    finally:
        conn.close()


def test_transactional_commits(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        with db_module.transactional(conn):
            conn.execute(
                """
                INSERT INTO events
                  (id, child_id, timestamp, raw_text, summary, type,
                   domains_json, emotions_json, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_ok",
                    "xiaoming",
                    "2026-05-19T10:00:00+08:00",
                    "原文",
                    "摘要",
                    "observation",
                    "[]",
                    "[]",
                    "manual",
                ),
            )
        row = conn.execute("SELECT id FROM events WHERE id = ?", ("evt_ok",)).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_db_path_respects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "nested" / "out.db"
    monkeypatch.setenv("BGH_DB", str(target))
    assert db_module.db_path() == target.resolve()
