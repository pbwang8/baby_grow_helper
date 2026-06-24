"""Runtime store adapter tests for the Phase 2.5 family trial."""

from __future__ import annotations

from contextlib import AbstractContextManager

import pytest
from src.core import db as db_module
from src.core import family as family_module
from src.core import runtime_store
from src.core.runtime_store import EventRecord, PostgresFamilyEventStore, RuntimeStoreError


def _event(event_id: str = "evt_store_1") -> EventRecord:
    return EventRecord(
        id=event_id,
        child_id="xiaoming",
        timestamp="2026-05-19T10:00:00+08:00",
        raw_text="今天小明自己刷牙",
        summary="小明自己刷牙",
        type="observation",
        domains=("self_care", "independence"),
        emotions=("proud",),
        context="家",
        source="manual",
        model_used="stub",
    )


def _seed_sqlite_family() -> None:
    conn = db_module.get_conn()
    try:
        family_module.ensure_family(
            conn,
            family_id="fam_001",
            name="Alpha Family",
            access_code="alpha-secret",
        )
        family_module.ensure_family(
            conn,
            family_id="fam_002",
            name="Beta Family",
            access_code="beta-secret",
        )
        conn.execute(
            "INSERT INTO children(id, family_id, name, birthday) VALUES (?, ?, ?, ?)",
            ("xiaoming", "fam_001", "小明", "2023-06-01"),
        )
    finally:
        conn.close()


def test_runtime_backend_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BGH_RUNTIME_DB_BACKEND", raising=False)
    monkeypatch.delenv("BGH_DATABASE_URL", raising=False)
    assert runtime_store.runtime_backend() == "sqlite"


def test_runtime_backend_uses_explicit_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BGH_RUNTIME_DB_BACKEND", "postgres")
    assert runtime_store.runtime_backend() == "postgres"


def test_runtime_backend_inferrs_postgres_from_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BGH_RUNTIME_DB_BACKEND", raising=False)
    monkeypatch.setenv("BGH_DATABASE_URL", "postgresql://example/db")
    assert runtime_store.runtime_backend() == "postgres"


def test_sqlite_family_event_store_roundtrip(tmp_db: object) -> None:
    _ = tmp_db
    _seed_sqlite_family()
    store = runtime_store.SQLiteFamilyEventStore()

    assert store.authenticate_family("alpha-secret") == ("fam_001", "Alpha Family")
    assert store.authenticate_family("wrong") is None
    assert store.child_exists(child_id="xiaoming", family_id="fam_001")
    assert not store.child_exists(child_id="xiaoming", family_id="fam_002")

    store.insert_event(_event(), family_id="fam_001")
    rows = store.list_events(child_id="xiaoming", family_id="fam_001", limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == "evt_store_1"
    assert rows[0]["domains_json"] == '["self_care", "independence"]'


def test_postgres_store_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BGH_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeStoreError, match="BGH_DATABASE_URL"):
        PostgresFamilyEventStore()


def test_postgres_store_requires_family_id_for_writes() -> None:
    store = PostgresFamilyEventStore(
        database_url="postgresql://example/db",
        connect_factory=lambda _url: _FakeConnection(),
    )
    with pytest.raises(RuntimeStoreError, match="family_id"):
        store.insert_event(_event(), family_id=None)


def test_postgres_store_auth_child_and_event_queries() -> None:
    fake = _FakeConnection()
    store = PostgresFamilyEventStore(
        database_url="postgresql://example/db",
        connect_factory=lambda _url: fake,
    )

    assert store.authenticate_family("alpha-secret") == ("fam_001", "Alpha Family")
    assert store.authenticate_family("wrong") is None
    assert store.child_exists(child_id="xiaoming", family_id="fam_001")

    store.insert_event(_event("evt_pg_1"), family_id="fam_001")
    rows = store.list_events(child_id="xiaoming", family_id="fam_001", limit=5)

    assert fake.committed
    assert rows == [
        {
            "id": "evt_pg_1",
            "child_id": "xiaoming",
            "timestamp": "2026-05-19T10:00:00+08:00",
            "raw_text": "今天小明自己刷牙",
            "summary": "小明自己刷牙",
            "type": "observation",
            "domains_json": ["self_care", "independence"],
            "emotions_json": ["proud"],
            "context": "家",
            "model_used": "stub",
        }
    ]


class _FakeConnection(AbstractContextManager["_FakeConnection"]):
    def __init__(self) -> None:
        self.committed = False
        self.events: list[dict[str, object]] = []
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed = True


class _FakeCursor(AbstractContextManager["_FakeCursor"]):
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn
        self._one: object | None = None
        self._many: list[dict[str, object]] = []

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        compact = " ".join(sql.split())
        self.conn.executed.append((compact, params))
        if "FROM families" in compact:
            self._many = [
                {
                    "id": "fam_001",
                    "name": "Alpha Family",
                    "access_code_hash": family_module.hash_access_code("alpha-secret"),
                }
            ]
            return
        if "FROM children" in compact:
            self._one = 1 if params == ("xiaoming", "fam_001") else None
            return
        if compact.startswith("INSERT INTO events"):
            domains = params[7]
            emotions = params[8]
            self.conn.events.append(
                {
                    "id": params[0],
                    "family_id": params[1],
                    "child_id": params[2],
                    "timestamp": params[3],
                    "raw_text": params[4],
                    "summary": params[5],
                    "type": params[6],
                    "domains_json": _json_list(domains),
                    "emotions_json": _json_list(emotions),
                    "context": params[9],
                    "source": params[10],
                    "model_used": params[11],
                }
            )
            return
        if "FROM events" in compact:
            family_id, child_id, _limit = params
            self._many = [
                {
                    key: row[key]
                    for key in (
                        "id",
                        "child_id",
                        "timestamp",
                        "raw_text",
                        "summary",
                        "type",
                        "domains_json",
                        "emotions_json",
                        "context",
                        "model_used",
                    )
                }
                for row in self.conn.events
                if row["family_id"] == family_id and row["child_id"] == child_id
            ]

    def fetchone(self) -> object | None:
        return self._one

    def fetchall(self) -> list[dict[str, object]]:
        return self._many


def _json_list(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    import json

    loaded = json.loads(value)
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]
