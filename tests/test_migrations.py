"""Phase 2.5 migration runner tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.core import migrations
from src.core.migrations import MigrationError


def test_detect_backend_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BGH_DATABASE_URL", raising=False)
    monkeypatch.delenv("BGH_DB", raising=False)
    assert migrations.detect_backend() == "sqlite"


def test_detect_backend_postgres_url() -> None:
    assert migrations.detect_backend("postgresql://user:pass@localhost/db") == "postgres"
    assert migrations.detect_backend("postgres://user:pass@localhost/db") == "postgres"


def test_list_migration_files_contains_phase25_sqlite() -> None:
    files = migrations.list_migration_files("sqlite")
    assert [f.version for f in files] == ["0001"]
    assert files[0].path.name == "0001_phase25_family_foundation.sql"


def test_list_migration_files_contains_postgres_initial_schema() -> None:
    files = migrations.list_migration_files("postgres")
    assert [f.version for f in files] == ["0001"]
    sql = files[0].path.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "family_id TEXT NOT NULL" in sql
    assert "CREATE TABLE IF NOT EXISTS weekly_insights" in sql


def test_apply_sqlite_migrations_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    first = migrations.apply_migrations(backend="sqlite", database=str(db_path))
    second = migrations.apply_migrations(backend="sqlite", database=str(db_path))

    assert first == ["0001"]
    assert second == []


def test_postgres_default_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BGH_DATABASE_URL", raising=False)
    with pytest.raises(MigrationError, match="BGH_DATABASE_URL"):
        migrations.default_database_for_backend("postgres")
