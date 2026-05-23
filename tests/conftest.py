"""Shared fixtures.

Key principle: every test gets a fresh on-disk SQLite under tmp_path. We never
touch ./data/babygrow.db from a test. We do this by patching BGH_DB env var
before any module that calls db_path() reads it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from src.core import db as db_module


@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point BGH_DB at a tmp file and create the schema."""
    target = tmp_path / "babygrow_test.db"
    monkeypatch.setenv("BGH_DB", str(target))
    db_module.init_db(target)
    yield target


@pytest.fixture()
def seeded_xiaoming(tmp_db: Path) -> Path:
    """Insert the synthetic child '小明' (NEVER 瑶瑶 in tests, per ADR 0001 F16)."""
    conn = db_module.get_conn(tmp_db)
    try:
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            ("xiaoming", "小明", "2023-06-01"),
        )
    finally:
        conn.close()
    return tmp_db
