"""Runtime storage adapter for the family mobile MVP.

Phase 2.5 starts the move from local SQLite to service-side Postgres without
rewriting the whole application at once. This module is the narrow first seam:
family-code login, event creation, and event listing.

SQLite remains the default for local development. Postgres is selected by
`BGH_RUNTIME_DB_BACKEND=postgres` or a postgres-shaped `BGH_DATABASE_URL`.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast

from src.core import db as sqlite_db
from src.core import family as family_module
from src.core.migrations import detect_backend

RuntimeBackend = Literal["sqlite", "postgres"]
Row = dict[str, object]
ConnectFactory = Callable[[str], AbstractContextManager[Any]]


class RuntimeStoreError(RuntimeError):
    """Runtime database adapter failed or was misconfigured."""


@dataclass(frozen=True)
class EventRecord:
    id: str
    child_id: str
    timestamp: str
    raw_text: str
    summary: str
    type: str
    domains: tuple[str, ...]
    emotions: tuple[str, ...]
    context: str
    source: str
    model_used: str

    def sqlite_row(self) -> Row:
        return {
            "id": self.id,
            "child_id": self.child_id,
            "timestamp": self.timestamp,
            "raw_text": self.raw_text,
            "summary": self.summary,
            "type": self.type,
            "domains_json": json.dumps(list(self.domains), ensure_ascii=False),
            "emotions_json": json.dumps(list(self.emotions), ensure_ascii=False),
            "context": self.context,
            "source": self.source,
            "model_used": self.model_used,
        }


class FamilyEventStore(Protocol):
    supports_background_embeddings: bool

    def authenticate_family(self, access_code: str) -> tuple[str, str] | None:
        """Return `(family_id, family_name)` when the access code is valid."""

    def child_exists(self, *, child_id: str, family_id: str | None) -> bool:
        """Return whether the caller can see this child."""

    def insert_event(self, event: EventRecord, *, family_id: str | None) -> None:
        """Persist one already-structured event."""

    def list_events(
        self, *, child_id: str, family_id: str | None, limit: int
    ) -> list[Row]:
        """Newest-first event listing."""


def runtime_backend() -> RuntimeBackend:
    """Return the configured runtime backend.

    `BGH_RUNTIME_DB_BACKEND` is explicit; otherwise we infer from
    `BGH_DATABASE_URL` so deployment only needs one database env var.
    """
    explicit = os.environ.get("BGH_RUNTIME_DB_BACKEND", "").strip().lower()
    if explicit == "sqlite":
        return "sqlite"
    if explicit == "postgres":
        return "postgres"
    return detect_backend(os.environ.get("BGH_DATABASE_URL"))


def get_family_event_store() -> FamilyEventStore:
    backend = runtime_backend()
    if backend == "sqlite":
        return SQLiteFamilyEventStore()
    return PostgresFamilyEventStore()


class SQLiteFamilyEventStore:
    supports_background_embeddings = True

    def authenticate_family(self, access_code: str) -> tuple[str, str] | None:
        conn = sqlite_db.get_conn()
        try:
            return family_module.find_family_by_access_code(conn, access_code)
        finally:
            conn.close()

    def child_exists(self, *, child_id: str, family_id: str | None) -> bool:
        conn = sqlite_db.get_conn()
        try:
            if family_id is None:
                row = conn.execute(
                    "SELECT 1 FROM children WHERE id = ?", (child_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM children WHERE id = ? AND family_id = ?",
                    (child_id, family_id),
                ).fetchone()
            return row is not None
        finally:
            conn.close()

    def insert_event(self, event: EventRecord, *, family_id: str | None) -> None:
        _ = family_id
        row = event.sqlite_row()
        conn = sqlite_db.get_conn()
        try:
            with sqlite_db.transactional(conn):
                conn.execute(
                    """
                    INSERT INTO events
                      (id, child_id, timestamp, raw_text, summary, type,
                       domains_json, emotions_json, context, source, model_used)
                    VALUES
                      (:id, :child_id, :timestamp, :raw_text, :summary, :type,
                       :domains_json, :emotions_json, :context, :source, :model_used)
                    """,
                    row,
                )
        finally:
            conn.close()

    def list_events(
        self, *, child_id: str, family_id: str | None, limit: int
    ) -> list[Row]:
        if family_id is None:
            sql = """
                SELECT id, child_id, timestamp, raw_text, summary, type,
                       domains_json, emotions_json, context, model_used
                FROM events
                WHERE child_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
            """
            params: tuple[object, ...] = (child_id, limit)
        else:
            sql = """
                SELECT e.id, e.child_id, e.timestamp, e.raw_text, e.summary, e.type,
                       e.domains_json, e.emotions_json, e.context, e.model_used
                FROM events e
                JOIN children c ON c.id = e.child_id
                WHERE e.child_id = ? AND c.family_id = ?
                ORDER BY e.timestamp DESC, e.id DESC
                LIMIT ?
            """
            params = (child_id, family_id, limit)

        conn = sqlite_db.get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()


class PostgresFamilyEventStore:
    supports_background_embeddings = False

    def __init__(
        self,
        *,
        database_url: str | None = None,
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        self._database_url = database_url or os.environ.get("BGH_DATABASE_URL", "")
        self._connect_factory = connect_factory
        if not self._database_url:
            raise RuntimeStoreError(
                "BGH_DATABASE_URL is required when runtime backend is postgres"
            )

    def authenticate_family(self, access_code: str) -> tuple[str, str] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id, name, access_code_hash FROM families")
            rows = cur.fetchall()
        for row in rows:
            mapped = _row_to_dict(row)
            stored_hash = str(mapped["access_code_hash"])
            if family_module.verify_access_code(access_code, stored_hash):
                return str(mapped["id"]), str(mapped["name"])
        return None

    def child_exists(self, *, child_id: str, family_id: str | None) -> bool:
        if family_id is None:
            return False
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM children WHERE id = %s AND family_id = %s",
                (child_id, family_id),
            )
            return cur.fetchone() is not None

    def insert_event(self, event: EventRecord, *, family_id: str | None) -> None:
        if family_id is None:
            raise RuntimeStoreError("Postgres event writes require family_id")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events
                      (id, family_id, child_id, timestamp, raw_text, summary, type,
                       domains_json, emotions_json, context, source, model_used)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s,
                       %s::jsonb, %s::jsonb, %s, %s, %s)
                    """,
                    (
                        event.id,
                        family_id,
                        event.child_id,
                        event.timestamp,
                        event.raw_text,
                        event.summary,
                        event.type,
                        json.dumps(list(event.domains), ensure_ascii=False),
                        json.dumps(list(event.emotions), ensure_ascii=False),
                        event.context,
                        event.source,
                        event.model_used,
                    ),
                )
            conn.commit()

    def list_events(
        self, *, child_id: str, family_id: str | None, limit: int
    ) -> list[Row]:
        if family_id is None:
            return []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT id, child_id, timestamp, raw_text, summary, type,
                           domains_json, emotions_json, context, model_used
                    FROM events
                    WHERE family_id = %s AND child_id = %s
                    ORDER BY timestamp DESC, id DESC
                    LIMIT %s
                    """,
                (family_id, child_id, limit),
            )
            rows = cur.fetchall()
        return [_row_to_dict(row) for row in rows]

    def _connect(self) -> AbstractContextManager[Any]:
        if self._connect_factory is not None:
            return self._connect_factory(self._database_url)
        try:
            psycopg: Any = importlib.import_module("psycopg")
            rows: Any = importlib.import_module("psycopg.rows")
        except ImportError as e:  # pragma: no cover - optional deploy dependency
            raise RuntimeStoreError(
                "Postgres runtime requires psycopg. Install it in the deploy image."
            ) from e
        return cast(
            AbstractContextManager[Any],
            psycopg.connect(self._database_url, row_factory=rows.dict_row),
        )


def _row_to_dict(row: Mapping[str, object] | object) -> Row:
    if isinstance(row, Mapping):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        row_any: Any = row
        return {str(key): row_any[key] for key in keys()}
    raise RuntimeStoreError(f"Unsupported database row shape: {type(row).__name__}")
