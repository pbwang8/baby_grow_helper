"""FastAPI surface for Phase 0.

Routes (PRD §2.1 #5):
  POST /events          — body {child_id, raw_text} → recorder → DB → return event
  GET  /events?child_id — newest-first listing
  GET  /health          — sqlite + ollama reachability
"""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from src.agents.recorder import Recorder, RecorderError, StructuredEvent
from src.core import db as db_module
from src.core.llm_client import LLMClient

app = FastAPI(
    title="BabyGrowHelper",
    version="0.0.1-phase0",
    description="Local-first parenting companion. Phase 0 skeleton.",
)


# ---- DI helpers -------------------------------------------------------


def get_recorder() -> Recorder:
    return Recorder()


def get_llm_client() -> LLMClient:
    return LLMClient()


# ---- request / response shapes ---------------------------------------


class CreateEventRequest(BaseModel):
    child_id: str = Field(min_length=1, max_length=64)
    raw_text: str = Field(min_length=1, max_length=4000)


class EventOut(BaseModel):
    id: str
    child_id: str
    timestamp: str
    raw_text: str
    summary: str
    type: str
    domains: list[str]
    emotions: list[str]
    context: str
    model_used: str

    @classmethod
    def from_event(cls, ev: StructuredEvent) -> "EventOut":
        return cls(
            id=ev.id,
            child_id=ev.child_id,
            timestamp=ev.timestamp,
            raw_text=ev.raw_text,
            summary=ev.summary,
            type=ev.type,
            domains=ev.domains,
            emotions=ev.emotions,
            context=ev.context,
            model_used=ev.model_used,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EventOut":
        return cls(
            id=row["id"],
            child_id=row["child_id"],
            timestamp=row["timestamp"],
            raw_text=row["raw_text"],
            summary=row["summary"],
            type=row["type"],
            domains=json.loads(row["domains_json"]),
            emotions=json.loads(row["emotions_json"]),
            context=row["context"] or "",
            model_used=row["model_used"] or "",
        )


class HealthOut(BaseModel):
    ok: bool
    sqlite: bool
    ollama: bool


# ---- routes ----------------------------------------------------------


@app.get("/health", response_model=HealthOut)
def health(llm: Annotated[LLMClient, Depends(get_llm_client)]) -> HealthOut:
    sqlite_ok = False
    try:
        conn = db_module.get_conn()
        try:
            conn.execute("SELECT 1").fetchone()
            sqlite_ok = True
        finally:
            conn.close()
    except sqlite3.Error:
        sqlite_ok = False
    ollama_ok = llm.ping_ollama()
    return HealthOut(ok=sqlite_ok and ollama_ok, sqlite=sqlite_ok, ollama=ollama_ok)


@app.post("/events", response_model=EventOut, status_code=201)
def create_event(
    body: CreateEventRequest,
    recorder: Annotated[Recorder, Depends(get_recorder)],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> EventOut:
    # X-User-Id is reserved for v1 auth — read but unused in Phase 0.
    _ = x_user_id

    conn = db_module.get_conn()
    try:
        child = conn.execute(
            "SELECT id FROM children WHERE id = ?", (body.child_id,)
        ).fetchone()
        if child is None:
            raise HTTPException(
                status_code=404,
                detail=f"child_id={body.child_id!r} not found. Run `make seed` first.",
            )

        try:
            event = recorder.record(child_id=body.child_id, raw_text=body.raw_text)
        except RecorderError as e:
            raise HTTPException(status_code=502, detail=f"Recorder failed: {e}") from e

        row = event.as_row()
        with db_module.transactional(conn):
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
        return EventOut.from_event(event)
    finally:
        conn.close()


@app.get("/events", response_model=list[EventOut])
def list_events(
    child_id: Annotated[str, Query(min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[EventOut]:
    conn = db_module.get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, child_id, timestamp, raw_text, summary, type,
                   domains_json, emotions_json, context, model_used
            FROM events
            WHERE child_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (child_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [EventOut.from_row(row) for row in rows]
