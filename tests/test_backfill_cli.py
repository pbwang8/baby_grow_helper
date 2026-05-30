"""Smoke tests for the backfill CLI (`src/scripts/backfill.py::main`).

Phase 1 baseline §6 flagged 61% coverage on this module — the gap is the
argparse `main()` path. We exercise it here against a tmp DB.

We deliberately do NOT exercise `--re-extract-signals` end-to-end (that
path constructs a real LLMClient and is covered by the integration suite).
We DO verify that:
  - happy path inserts rows and exits 0
  - unknown child surfaces BackfillError
  - bad JSONL surfaces BackfillError
  - empty file is a no-op (warning) but exit 0
"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.core import db as db_module
from src.scripts.backfill import BackfillError, main


def _seed_xiaoming(db_path: Path) -> None:
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            ("xiaoming", "小明", "2023-06-01"),
        )
        conn.commit()
    finally:
        conn.close()


def _good_line() -> str:
    return (
        '{"timestamp":"2026-05-15T10:00:00+08:00",'
        '"summary":"sm","type":"observation",'
        '"domains":["music"],"emotions":["proud"],"context":""}'
    )


def test_cli_main_inserts_and_returns_zero(
    tmp_db: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _seed_xiaoming(tmp_db)
    fixture = tmp_path / "in.jsonl"
    fixture.write_text(_good_line() + "\n", encoding="utf-8")

    import logging
    with caplog.at_level(logging.INFO, logger="src.scripts.backfill"):
        rc = main(["--child", "xiaoming", "--file", str(fixture)])
    assert rc == 0

    # the row landed
    conn = db_module.get_conn(tmp_db)
    try:
        rows = conn.execute(
            "SELECT id, source FROM events WHERE child_id='xiaoming'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["source"] == "backfill"
    # logger output should mention insertion count
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "inserted 1" in messages
    assert "parsed 1 records" in messages


def test_cli_main_empty_file_is_noop(
    tmp_db: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _seed_xiaoming(tmp_db)
    fixture = tmp_path / "empty.jsonl"
    fixture.write_text("\n# comment only\n\n", encoding="utf-8")

    import logging
    with caplog.at_level(logging.WARNING, logger="src.scripts.backfill"):
        rc = main(["--child", "xiaoming", "--file", str(fixture)])
    assert rc == 0

    conn = db_module.get_conn(tmp_db)
    try:
        rows = conn.execute(
            "SELECT id FROM events WHERE child_id='xiaoming'"
        ).fetchall()
    finally:
        conn.close()
    assert rows == []
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "nothing to insert" in messages


def test_cli_main_unknown_child_raises(tmp_db: Path, tmp_path: Path) -> None:
    """No `xiaoming` seeded → insert_records should raise BackfillError.

    We don't catch in main(), so it bubbles. That's fine for a CLI:
    argparse already handles --help; runtime errors should be loud.
    """
    fixture = tmp_path / "in.jsonl"
    fixture.write_text(_good_line() + "\n", encoding="utf-8")
    with pytest.raises(BackfillError, match="not found"):
        main(["--child", "ghost", "--file", str(fixture)])


def test_cli_main_bad_json_raises(tmp_db: Path, tmp_path: Path) -> None:
    _seed_xiaoming(tmp_db)
    fixture = tmp_path / "bad.jsonl"
    fixture.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(BackfillError, match="invalid JSON"):
        main(["--child", "xiaoming", "--file", str(fixture)])


def test_cli_main_missing_file_raises(tmp_db: Path, tmp_path: Path) -> None:
    _seed_xiaoming(tmp_db)
    with pytest.raises(BackfillError, match="file not found"):
        main(
            [
                "--child",
                "xiaoming",
                "--file",
                str(tmp_path / "does_not_exist.jsonl"),
            ]
        )
