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
    expected = {
        "children",
        "events",
        "event_embeddings",
        "usage_log",
        "signals",
        "weekly_insights",
        "insight_feedback",
    }
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
        with pytest.raises(RuntimeError, match="boom"), db_module.transactional(conn):
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


# ---- Phase 1: signals table -------------------------------------------------


def test_signals_roundtrip(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        conn.execute(
            """
            INSERT INTO signals
              (id, child_id, signal_type, domains_json, intensity,
               child_age_months, delta_from_last_period, confidence,
               first_seen_at, last_seen_at, evidence_event_ids_json,
               status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sig_20260519_001",
                "xiaoming",
                "interest_pattern",
                '["music"]',
                0.7,
                35,
                0.4,
                0.8,
                "2026-05-05T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
                '["evt_a","evt_b","evt_c"]',
                "active",
                "三次哼唱不同曲调",
            ),
        )
        row = conn.execute(
            "SELECT * FROM signals WHERE id = ?", ("sig_20260519_001",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["signal_type"] == "interest_pattern"
    assert row["status"] == "active"
    assert row["child_age_months"] == 35


def test_signals_foreign_key_enforced(tmp_db: Path) -> None:
    conn = db_module.get_conn(tmp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO signals
                  (id, child_id, signal_type, intensity, child_age_months,
                   confidence, first_seen_at, last_seen_at,
                   evidence_event_ids_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sig_orphan",
                    "ghost_child",
                    "interest_pattern",
                    0.5,
                    30,
                    0.5,
                    "2026-05-05T10:00:00+08:00",
                    "2026-05-19T10:00:00+08:00",
                    '["evt_a","evt_b"]',
                ),
            )
    finally:
        conn.close()


# ---- Phase 2: weekly_insights + insight_feedback ---------------------------


def _insert_insight(
    conn: sqlite3.Connection,
    *,
    insight_id: str,
    child_id: str = "xiaoming",
    week_start: str = "2026-05-18",
    week_end: str = "2026-05-25",
    version: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO weekly_insights
          (id, child_id, week_start, week_end, version,
           child_age_months, sections_json, open_questions_json,
           sources_used_json, backend, model_used, tokens_in, tokens_out)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            insight_id,
            child_id,
            week_start,
            week_end,
            version,
            35,
            '[{"axis":"highlight","body":"…"}]',
            '["上周第三次出现拒绝刷牙——阶段性还是有诱因？"]',
            '["sig_20260520_001","evt_a","evt_b"]',
            "claude",
            "claude-sonnet-4",
            3500,
            850,
        ),
    )


def test_weekly_insights_roundtrip(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_insight(conn, insight_id="ins_uuid_001")
        row = conn.execute(
            "SELECT * FROM weekly_insights WHERE id = ?", ("ins_uuid_001",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["child_id"] == "xiaoming"
    assert row["version"] == 1
    assert row["backend"] == "claude"
    assert row["tokens_in"] == 3500
    assert row["created_at"]  # filled by default


def test_weekly_insights_foreign_key_enforced(tmp_db: Path) -> None:
    conn = db_module.get_conn(tmp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_insight(conn, insight_id="ins_orphan", child_id="ghost")
    finally:
        conn.close()


def test_weekly_insights_unique_per_child_week_version(seeded_xiaoming: Path) -> None:
    """PRD §3.5: business uniqueness is (child_id, week_start, version).

    Same week with bumped version must succeed; same triple must collide.
    """
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_insight(conn, insight_id="ins_uuid_v1", version=1)
        # bumping version is fine
        _insert_insight(conn, insight_id="ins_uuid_v2", version=2)
        # same (child, week_start, version) collides
        with pytest.raises(sqlite3.IntegrityError):
            _insert_insight(conn, insight_id="ins_uuid_dup", version=1)
    finally:
        conn.close()


def test_insight_feedback_cascades_on_insight_delete(seeded_xiaoming: Path) -> None:
    """ON DELETE CASCADE keeps feedback consistent when an insight is removed."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_insight(conn, insight_id="ins_for_fb")
        conn.execute(
            """
            INSERT INTO insight_feedback
              (id, insight_id, section_idx, accuracy, value, free_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("fb_uuid_001", "ins_for_fb", 0, "accurate", "inspiring", "好"),
        )
        conn.execute("DELETE FROM weekly_insights WHERE id = ?", ("ins_for_fb",))
        row = conn.execute(
            "SELECT id FROM insight_feedback WHERE id = ?", ("fb_uuid_001",)
        ).fetchone()
        assert row is None  # cascaded
    finally:
        conn.close()


def test_insight_feedback_allows_partial_dimensions(seeded_xiaoming: Path) -> None:
    """A parent might rate accuracy without value (or vice versa)."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_insight(conn, insight_id="ins_for_partial")
        conn.execute(
            """
            INSERT INTO insight_feedback
              (id, insight_id, section_idx, accuracy)
            VALUES (?, ?, ?, ?)
            """,
            ("fb_partial", "ins_for_partial", 1, "unsure"),
        )
        row = conn.execute(
            "SELECT accuracy, value, free_text FROM insight_feedback WHERE id = ?",
            ("fb_partial",),
        ).fetchone()
    finally:
        conn.close()
    assert row["accuracy"] == "unsure"
    assert row["value"] is None
    assert row["free_text"] is None
