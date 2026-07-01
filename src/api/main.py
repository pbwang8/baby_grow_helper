"""FastAPI surface.

Phase 0 routes (PRD `prd/phase0-skeleton.md` §2.1#5):
  POST /events          — body {child_id, raw_text} → recorder → DB → return event
  GET  /events?child_id — newest-first listing
  GET  /health          — sqlite + ollama reachability

Phase 1 additions (PRD `prd/phase1-signals.md` §2.1#2 + #5):
  GET  /signals?child_id              — list active signals for the heatmap/timeline
  POST /signals/extract?child_id=...  — manual trigger of the extractor
  GET  /heatmap?child_id              — per-(age_month, domain) intensity grid

Phase 2 additions (PRD `prd/phase2-weekly-insight.md` §2.1#4 + #6):
  POST /insights/generate?child_id=&week_start=YYYY-MM-DD  — write a weekly insight
  GET  /insights/:id                                       — fetch one insight by id
  GET  /insights?child_id=                                 — list (newest first)
  POST /insights/:id/feedback                              — section-level feedback
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import uuid
from collections.abc import Mapping
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.agents.context_compressor import (
    ContextCompressorError,
    compress_week_context,
)
from src.agents.insight_writer import (
    InsightSection,
    InsightWriterError,
    WeeklyInsight,
    write_weekly_insight,
)
from src.agents.recorder import Recorder, RecorderError, StructuredEvent
from src.agents.signal_extractor import SignalExtractor, SignalExtractorError
from src.core import db as db_module
from src.core import embeddings as emb_module
from src.core import family as family_module
from src.core import runtime_store as store_module
from src.core.llm_client import LLMClient
from src.core.models import Signal
from src.core.signal_delta import HeatmapCell

app = FastAPI(
    title="BabyGrowHelper",
    version="0.1.0-phase1",
    description="Local-first parenting companion. Phase 1 — signals layer.",
)


def _cors_origins() -> list[str]:
    defaults = ["http://localhost:3000", "http://127.0.0.1:3000"]
    raw = os.environ.get("BGH_CORS_ORIGINS", "").strip()
    if not raw:
        return defaults
    extra = [item.strip() for item in raw.split(",") if item.strip()]
    return [*defaults, *extra]


# Phase 2.5 family trial can run on a LAN host or a small cloud VM.
# Additional origins are configured via BGH_CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- DI helpers -------------------------------------------------------


def get_recorder() -> Recorder:
    return Recorder()


def get_llm_client() -> LLMClient:
    return LLMClient()


def get_family_event_store() -> store_module.FamilyEventStore:
    return store_module.get_family_event_store()


def get_current_family_id(
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
    x_family_code: Annotated[
        str | None, Header(alias=family_module.FAMILY_CODE_HEADER)
    ] = None,
) -> str | None:
    """Resolve the request family when Phase 2.5 family auth is enabled.

    Local development keeps auth disabled by default so existing single-user
    flows keep working. In deployed family mode, every protected route must
    carry `X-Family-Code`.
    """
    if not family_module.family_auth_required():
        return None
    if not x_family_code:
        raise HTTPException(
            status_code=401,
            detail=f"Missing {family_module.FAMILY_CODE_HEADER} header.",
        )
    found = store.authenticate_family(x_family_code)
    if found is None:
        raise HTTPException(status_code=403, detail="Invalid family access code.")
    return found[0]


# ---- request / response shapes ---------------------------------------


class CreateEventRequest(BaseModel):
    child_id: str = Field(min_length=1, max_length=64)
    raw_text: str = Field(min_length=1, max_length=4000)
    occurred_at: str | None = Field(default=None, min_length=10, max_length=32)


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
    def from_event(cls, ev: StructuredEvent) -> EventOut:
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
    def from_row(cls, row: sqlite3.Row | Mapping[str, object]) -> EventOut:
        return cls(
            id=_row_str(row, "id"),
            child_id=_row_str(row, "child_id"),
            timestamp=_row_str(row, "timestamp"),
            raw_text=_row_str(row, "raw_text"),
            summary=_row_str(row, "summary"),
            type=_row_str(row, "type"),
            domains=_json_list(row["domains_json"]),
            emotions=_json_list(row["emotions_json"]),
            context=_row_str(row, "context"),
            model_used=_row_str(row, "model_used"),
        )


class HealthOut(BaseModel):
    ok: bool
    sqlite: bool
    ollama: bool


class FamilyAuthRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=128)


class ChildOut(BaseModel):
    id: str
    name: str
    birthday: str

    @classmethod
    def from_record(cls, child: store_module.ChildRecord) -> ChildOut:
        return cls(id=child.id, name=child.name, birthday=child.birthday)


class FamilyAuthOut(BaseModel):
    family_id: str
    family_name: str
    children: list[ChildOut]


class CreateChildRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    birthday: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD


class TrialFeedbackRequest(BaseModel):
    child_id: str | None = Field(default=None, max_length=64)
    page: str = Field(min_length=1, max_length=80)
    category: Literal["bug", "idea", "confusing", "other"] = "other"
    message: str = Field(min_length=1, max_length=2000)
    contact: str | None = Field(default=None, max_length=120)


class TrialFeedbackOut(BaseModel):
    id: str
    child_id: str | None
    page: str
    category: str
    created_at: str


def _row_str(row: sqlite3.Row | Mapping[str, object], key: str) -> str:
    value = row[key]
    if value is None:
        return ""
    return str(value)


def _json_list(value: object) -> list[str]:
    loaded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]


def _validate_iso_date(value: str, *, field: str) -> None:
    try:
        dt.date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be YYYY-MM-DD, got {value!r}",
        ) from e


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def _normalize_event_timestamp(occurred_at: str | None) -> str | None:
    """Normalize optional historical entry date/time into recorder timestamp.

    `/log` usually records "now". For milestone backfill, the user can provide
    a date (`YYYY-MM-DD`) or an ISO-ish datetime. A date-only value is anchored
    to noon China time so it sorts into the right day without pretending we know
    the exact hour.
    """
    if occurred_at is None:
        return None
    value = occurred_at.strip()
    if not value:
        return None
    if len(value) == 10:
        _validate_iso_date(value, field="occurred_at")
        return f"{value}T12:00:00+08:00"
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                "occurred_at must be YYYY-MM-DD or ISO datetime, "
                f"got {occurred_at!r}"
            ),
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return parsed.isoformat()


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


def _embed_event_safe(event_id: str, text: str) -> None:
    """Wrapper used as a BackgroundTask. Swallows errors so a flaky
    embedder never breaks the user-visible POST /events response.

    PRD phase1-signals §2.1#3: "每条 event 落库后异步算嵌入".
    """
    try:
        emb_module.embed_and_store_event(event_id, text)
    except Exception:  # pragma: no cover - defensive
        # Logged for ops; the user already got their event back.
        import logging

        logging.getLogger(__name__).warning(
            "background embed failed for event %s", event_id, exc_info=True
        )


def _child_row(
    conn: sqlite3.Connection, *, child_id: str, family_id: str | None
) -> sqlite3.Row | None:
    if family_id is None:
        row: sqlite3.Row | None = conn.execute(
            "SELECT id FROM children WHERE id = ?", (child_id,)
        ).fetchone()
        return row
    row = conn.execute(
        "SELECT id FROM children WHERE id = ? AND family_id = ?",
        (child_id, family_id),
    ).fetchone()
    return row


def _require_child_visible(
    conn: sqlite3.Connection, *, child_id: str, family_id: str | None
) -> None:
    if family_id is None:
        return
    if _child_row(conn, child_id=child_id, family_id=family_id) is None:
        raise HTTPException(status_code=404, detail=f"child_id={child_id!r} not found")


@app.post("/auth/family", response_model=FamilyAuthOut)
def authenticate_family(
    body: FamilyAuthRequest,
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
) -> FamilyAuthOut:
    """Verify a family access code and return the scoped family id.

    This is the Phase 2.5 minimal login primitive. It is intentionally not a
    commercial account system.
    """
    found = store.authenticate_family(body.access_code)
    if found is None:
        raise HTTPException(status_code=403, detail="Invalid family access code.")
    family_id, family_name = found
    children = store.list_children(family_id=family_id)
    return FamilyAuthOut(
        family_id=family_id,
        family_name=family_name,
        children=[ChildOut.from_record(child) for child in children],
    )


@app.get("/children", response_model=list[ChildOut])
def list_children(
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
) -> list[ChildOut]:
    children = store.list_children(family_id=family_id)
    return [ChildOut.from_record(child) for child in children]


@app.post("/children", response_model=ChildOut, status_code=201)
def create_child(
    body: CreateChildRequest,
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
) -> ChildOut:
    """Create a child profile inside the current invited family.

    Phase 2.5 keeps auth intentionally small: family access code first, child
    profile second. No phone/SMS account system is introduced here.
    """
    _validate_iso_date(body.birthday, field="birthday")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be blank")
    child = store.create_child(
        family_id=family_id,
        child_id=f"child_{uuid.uuid4().hex[:12]}",
        name=name,
        birthday=body.birthday,
    )
    return ChildOut.from_record(child)


@app.post("/events", response_model=EventOut, status_code=201)
def create_event(
    body: CreateEventRequest,
    background: BackgroundTasks,
    recorder: Annotated[Recorder, Depends(get_recorder)],
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> EventOut:
    # X-User-Id is reserved for v1 auth — read but unused in Phase 0.
    _ = x_user_id

    if not store.child_exists(child_id=body.child_id, family_id=family_id):
        raise HTTPException(
            status_code=404,
            detail=f"child_id={body.child_id!r} not found. Run `make seed` first.",
        )

    try:
        event = recorder.record(
            child_id=body.child_id,
            raw_text=body.raw_text,
            timestamp=_normalize_event_timestamp(body.occurred_at),
        )
    except RecorderError as e:
        raise HTTPException(status_code=502, detail=f"Recorder failed: {e}") from e

    store.insert_event(
        store_module.EventRecord(
            id=event.id,
            child_id=event.child_id,
            timestamp=event.timestamp,
            raw_text=event.raw_text,
            summary=event.summary,
            type=event.type,
            domains=tuple(event.domains),
            emotions=tuple(event.emotions),
            context=event.context,
            source="manual",
            model_used=event.model_used,
        ),
        family_id=family_id,
    )
    if store.supports_background_embeddings:
        # Fire-and-forget: embedding takes ~50-200ms on M-series CPU; we
        # don't want to make the user wait for it on every record.
        background.add_task(_embed_event_safe, event.id, event.summary)
    return EventOut.from_event(event)


@app.get("/events", response_model=list[EventOut])
def list_events(
    child_id: Annotated[str, Query(min_length=1, max_length=64)],
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[EventOut]:
    if family_id is not None and not store.child_exists(
        child_id=child_id, family_id=family_id
    ):
        raise HTTPException(status_code=404, detail=f"child_id={child_id!r} not found")
    rows = store.list_events(child_id=child_id, family_id=family_id, limit=limit)
    return [EventOut.from_row(row) for row in rows]


@app.post("/feedback", response_model=TrialFeedbackOut, status_code=201)
def submit_trial_feedback(
    body: TrialFeedbackRequest,
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
) -> TrialFeedbackOut:
    """Store invited-family product feedback.

    This is separate from `/insights/{id}/feedback`, which rates one weekly
    insight section. Here the tester can report app-level friction.
    """
    child_id = body.child_id.strip() if body.child_id else None
    if child_id and not store.child_exists(child_id=child_id, family_id=family_id):
        raise HTTPException(status_code=404, detail=f"child_id={child_id!r} not found")

    created_at = _utc_now_iso()
    feedback = store_module.TrialFeedbackRecord(
        id=uuid.uuid4().hex,
        child_id=child_id or None,
        page=body.page.strip(),
        category=body.category,
        message=body.message.strip(),
        contact=(body.contact or "").strip(),
        created_at=created_at,
    )
    if not feedback.page or not feedback.message:
        raise HTTPException(status_code=422, detail="page and message must not be blank")
    store.submit_trial_feedback(feedback, family_id=family_id)
    return TrialFeedbackOut(
        id=feedback.id,
        child_id=feedback.child_id,
        page=feedback.page,
        category=feedback.category,
        created_at=feedback.created_at,
    )


# ---- Phase 1: signals + heatmap routes -------------------------------


class SignalOut(BaseModel):
    id: str
    child_id: str
    signal_type: str
    domains: list[str]
    intensity: float
    child_age_months: int
    delta_from_last_period: float | None
    confidence: float
    first_seen_at: str
    last_seen_at: str
    evidence_event_ids: list[str]
    status: str
    notes: str

    @classmethod
    def from_signal(cls, sig: Signal) -> SignalOut:
        return cls(
            id=sig.id,
            child_id=sig.child_id,
            signal_type=sig.signal_type,
            domains=sig.domains,
            intensity=sig.intensity,
            child_age_months=sig.child_age_months,
            delta_from_last_period=sig.delta_from_last_period,
            confidence=sig.confidence,
            first_seen_at=sig.first_seen_at,
            last_seen_at=sig.last_seen_at,
            evidence_event_ids=sig.evidence_event_ids,
            status=sig.status,
            notes=sig.notes,
        )


class HeatmapCellOut(BaseModel):
    age_months: int
    domain: str
    intensity: float
    event_count: int

    @classmethod
    def from_cell(cls, c: HeatmapCell) -> HeatmapCellOut:
        return cls(
            age_months=c.age_months,
            domain=c.domain,
            intensity=c.intensity,
            event_count=c.event_count,
        )


def get_signal_extractor() -> SignalExtractor:
    return SignalExtractor()


@app.get("/signals", response_model=list[SignalOut])
def list_signals(
    child_id: Annotated[str, Query(min_length=1, max_length=64)],
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    status: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SignalOut]:
    """List signals for a child, newest first.

    `status` filter is exact-match against the closed set
    {active, dormant, dismissed}; missing → all.
    """
    sql = """
        SELECT id, child_id, signal_type, domains_json, intensity,
               child_age_months, delta_from_last_period, confidence,
               first_seen_at, last_seen_at, evidence_event_ids_json,
               status, notes
        FROM signals
        WHERE child_id = ?
    """
    params: list[object] = [child_id]
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY last_seen_at DESC, id DESC LIMIT ?"
    params.append(limit)

    conn = db_module.get_conn()
    try:
        _require_child_visible(conn, child_id=child_id, family_id=family_id)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [SignalOut.from_signal(Signal.from_row(dict(r))) for r in rows]


@app.post("/signals/extract", response_model=list[SignalOut], status_code=201)
def extract_signals(
    child_id: Annotated[str, Query(min_length=1, max_length=64)],
    extractor: Annotated[SignalExtractor, Depends(get_signal_extractor)],
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    window_days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> list[SignalOut]:
    """Manual trigger for the signal extractor.

    PRD §2.1#2: extraction is NOT cron-driven in Phase 1; the parent
    (or the backfill script) decides when to refresh the signal layer.
    """
    conn = db_module.get_conn()
    try:
        _require_child_visible(conn, child_id=child_id, family_id=family_id)
    finally:
        conn.close()
    try:
        signals = extractor.extract_for_child(
            child_id=child_id, window_days=window_days
        )
    except SignalExtractorError as e:
        # "child not found" maps to 404, everything else to 502 (the LLM blew up).
        if "not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=502, detail=f"Extractor failed: {e}") from e
    return [SignalOut.from_signal(s) for s in signals]


# ---- Phase 2: weekly insights + feedback -----------------------------


class InsightSectionOut(BaseModel):
    axis: Literal["highlight", "change_over_time", "next_week_focus", "open_questions"]
    title: str
    body: str
    sources_used: list[str]

    @classmethod
    def from_section(cls, s: InsightSection) -> InsightSectionOut:
        return cls(
            axis=s.axis,
            title=s.title,
            body=s.body,
            sources_used=list(s.sources_used),
        )


class WeeklyInsightOut(BaseModel):
    id: str
    child_id: str
    week_start: str
    week_end: str
    version: int
    child_age_months: int
    sections: list[InsightSectionOut]
    open_questions: list[str]
    sources_used: list[str]
    backend: str
    model_used: str
    tokens_in: int
    tokens_out: int
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> WeeklyInsightOut:
        sections_raw = json.loads(row["sections_json"])
        return cls(
            id=row["id"],
            child_id=row["child_id"],
            week_start=row["week_start"],
            week_end=row["week_end"],
            version=row["version"],
            child_age_months=row["child_age_months"],
            sections=[InsightSectionOut(**s) for s in sections_raw],
            open_questions=json.loads(row["open_questions_json"]),
            sources_used=json.loads(row["sources_used_json"]),
            backend=row["backend"],
            model_used=row["model_used"],
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            created_at=row["created_at"],
        )


class GenerateInsightRequest(BaseModel):
    child_id: str = Field(min_length=1, max_length=64)
    week_start: str = Field(min_length=10, max_length=10)  # YYYY-MM-DD
    backend: Literal["claude", "local-fallback"] = "claude"


class FeedbackRequest(BaseModel):
    section_idx: int = Field(ge=0, le=15)
    accuracy: Literal["accurate", "inaccurate", "unsure"] | None = None
    value: Literal["inspiring", "unhelpful", "missed_point"] | None = None
    free_text: str | None = Field(default=None, max_length=500)


class FeedbackOut(BaseModel):
    id: str
    insight_id: str
    section_idx: int
    accuracy: str | None
    value: str | None
    free_text: str | None
    created_at: str


def _next_version(conn: sqlite3.Connection, child_id: str, week_start: str) -> int:
    """PRD §3.5: regeneration bumps version. Find the max + 1."""
    row = conn.execute(
        """
        SELECT COALESCE(MAX(version), 0) AS v
        FROM weekly_insights
        WHERE child_id = ? AND week_start = ?
        """,
        (child_id, week_start),
    ).fetchone()
    return int(row["v"]) + 1


def _persist_insight(
    conn: sqlite3.Connection,
    *,
    insight: WeeklyInsight,
    version: int,
) -> str:
    """Write a WeeklyInsight to disk. Returns the row's primary key.

    The Agent layer mints its own UUID4 for `insight.id`; we ALWAYS use
    that as the row PK so callers can correlate the response and the row.
    """
    with db_module.transactional(conn):
        conn.execute(
            """
            INSERT INTO weekly_insights
              (id, child_id, week_start, week_end, version,
               child_age_months, sections_json, open_questions_json,
               sources_used_json, backend, model_used, tokens_in, tokens_out)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                insight.id,
                insight.child_id,
                insight.week_start,
                insight.week_end,
                version,
                insight.child_age_months,
                json.dumps(
                    [s.model_dump() for s in insight.sections],
                    ensure_ascii=False,
                ),
                json.dumps(insight.open_questions, ensure_ascii=False),
                json.dumps(insight.sources_used, ensure_ascii=False),
                insight.backend,
                insight.model_used,
                insight.tokens_in,
                insight.tokens_out,
            ),
        )
    return insight.id


@app.post("/insights/generate", response_model=WeeklyInsightOut, status_code=201)
def generate_insight(
    body: GenerateInsightRequest,
    family_id: Annotated[str | None, Depends(get_current_family_id)],
) -> WeeklyInsightOut:
    """Compose + persist a weekly insight for one (child, week).

    PRD §2.1#6: parent-triggered, NOT a cron. Same week regenerated with
    a different version (UNIQUE INDEX ON child_id, week_start, version).
    """
    try:
        week_start = dt.date.fromisoformat(body.week_start)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=f"week_start must be YYYY-MM-DD, got {body.week_start!r}",
        ) from e

    conn = db_module.get_conn()
    try:
        _require_child_visible(conn, child_id=body.child_id, family_id=family_id)
        # Build the compressed context (this is also where the child-not-found
        # and non-Monday checks live).
        try:
            ctx = compress_week_context(body.child_id, week_start, conn=conn)
        except ContextCompressorError as e:
            status = 404 if "not found" in str(e) else 422
            raise HTTPException(status_code=status, detail=str(e)) from e

        # Run the writer. InsightWriter handles retry+degrade internally,
        # so we only see InsightWriterError if `_load_prompt` itself fails.
        try:
            insight = write_weekly_insight(ctx, backend=body.backend)
        except InsightWriterError as e:
            raise HTTPException(
                status_code=502, detail=f"Writer failed: {e}"
            ) from e

        version = _next_version(conn, body.child_id, body.week_start)
        _persist_insight(conn, insight=insight, version=version)

        # Read back so the response includes server-side `created_at`.
        row = conn.execute(
            "SELECT * FROM weekly_insights WHERE id = ?", (insight.id,)
        ).fetchone()
        return WeeklyInsightOut.from_row(row)
    finally:
        conn.close()


@app.get("/insights", response_model=list[WeeklyInsightOut])
def list_insights(
    child_id: Annotated[str, Query(min_length=1, max_length=64)],
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> list[WeeklyInsightOut]:
    conn = db_module.get_conn()
    try:
        _require_child_visible(conn, child_id=child_id, family_id=family_id)
        rows = conn.execute(
            """
            SELECT * FROM weekly_insights
            WHERE child_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (child_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [WeeklyInsightOut.from_row(r) for r in rows]


@app.get("/insights/{insight_id}", response_model=WeeklyInsightOut)
def get_insight(
    insight_id: str,
    family_id: Annotated[str | None, Depends(get_current_family_id)],
) -> WeeklyInsightOut:
    conn = db_module.get_conn()
    try:
        if family_id is None:
            row = conn.execute(
                "SELECT * FROM weekly_insights WHERE id = ?", (insight_id,)
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT wi.*
                FROM weekly_insights wi
                JOIN children c ON c.id = wi.child_id
                WHERE wi.id = ? AND c.family_id = ?
                """,
                (insight_id, family_id),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"insight {insight_id!r} not found")
    return WeeklyInsightOut.from_row(row)


@app.post("/insights/{insight_id}/feedback", response_model=FeedbackOut, status_code=201)
def post_feedback(
    insight_id: str,
    body: FeedbackRequest,
    family_id: Annotated[str | None, Depends(get_current_family_id)],
) -> FeedbackOut:
    """PRD §3.6: section-level feedback. accuracy/value both nullable
    (parent may submit only one dimension)."""
    if body.accuracy is None and body.value is None and not body.free_text:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of accuracy / value / free_text.",
        )

    conn = db_module.get_conn()
    try:
        if family_id is None:
            ins = conn.execute(
                "SELECT id FROM weekly_insights WHERE id = ?", (insight_id,)
            ).fetchone()
        else:
            ins = conn.execute(
                """
                SELECT wi.id
                FROM weekly_insights wi
                JOIN children c ON c.id = wi.child_id
                WHERE wi.id = ? AND c.family_id = ?
                """,
                (insight_id, family_id),
            ).fetchone()
        if ins is None:
            raise HTTPException(
                status_code=404, detail=f"insight {insight_id!r} not found"
            )
        fb_id = uuid.uuid4().hex
        with db_module.transactional(conn):
            conn.execute(
                """
                INSERT INTO insight_feedback
                  (id, insight_id, section_idx, accuracy, value, free_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fb_id,
                    insight_id,
                    body.section_idx,
                    body.accuracy,
                    body.value,
                    body.free_text,
                ),
            )
        row = conn.execute(
            "SELECT * FROM insight_feedback WHERE id = ?", (fb_id,)
        ).fetchone()
    finally:
        conn.close()
    return FeedbackOut(
        id=row["id"],
        insight_id=row["insight_id"],
        section_idx=row["section_idx"],
        accuracy=row["accuracy"],
        value=row["value"],
        free_text=row["free_text"],
        created_at=row["created_at"],
    )


@app.get("/heatmap", response_model=list[HeatmapCellOut])
def get_heatmap(
    child_id: Annotated[str, Query(min_length=1, max_length=64)],
    family_id: Annotated[str | None, Depends(get_current_family_id)],
    store: Annotated[
        store_module.FamilyEventStore, Depends(get_family_event_store)
    ],
    domains: Annotated[list[str] | None, Query()] = None,
) -> list[HeatmapCellOut]:
    """Per-(child_age_months, domain) intensity grid.

    PRD §2.1#5: x-axis is **child age in months**, NOT calendar date.
    The aggregation uses the runtime store so deployed Postgres family trials
    do not fall back to local SQLite.
    """
    if family_id is not None and not store.child_exists(
        child_id=child_id, family_id=family_id
    ):
        raise HTTPException(status_code=404, detail=f"child_id={child_id!r} not found")
    cells = store.heatmap_data(child_id=child_id, family_id=family_id, domains=domains)
    return [HeatmapCellOut.from_cell(c) for c in cells]
