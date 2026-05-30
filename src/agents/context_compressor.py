"""Phase 2 M2.1 — context compressor.

Why this exists (PRD prd/phase2-weekly-insight.md §2.1#1, §3 locked):
  Cloud insight Agent cannot eat raw event streams. ~30-50 events/week
  ≈ 15-25k tokens, way past CLAUDE.md §5's 100k/month budget. This Agent
  is the first gate before any cloud call.

Compression strategy (PRD §3 — locked, no OR branches):
  Signals layer  : ALL active signals (last_seen_at ∈ week) enter as
                   ≤120-char one-liners.
  Events layer   :
    - type=milestone events ALWAYS preserved (PRD §2.1#7 hard test).
    - Each active signal contributes up to 2 evidence events.
    - Domains uncovered by any signal each get 1-2 events.
    - Total highlights capped at 8 EXCEPT milestones (which never trim).
  Drop raw_text  : recorder.summary already has the compressed text.
  Validation     : output is a Pydantic frozen model, so callers get
                   parse-time guarantees same as Phase 1's `Signal`.

Why not use parse_json_strict (PRD wording):
  parse_json_strict is for LLM outputs that arrive as text and need
  defensive JSON-cleanup. Compressor's output is built in-process from
  trusted DB rows; Pydantic's `model_validate` is the right gate.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from collections import defaultdict
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.core import db as db_module
from src.core.models import Signal, compute_age_months
from src.core.signal_delta import compute_period_delta

logger = logging.getLogger(__name__)

# ---- knobs ----------------------------------------------------------------

DEFAULT_MAX_TOKENS: Final[int] = 4000
"""PRD §2.1#1: caller-overridable, default 4k. Soft warning if exceeded."""

MAX_EVIDENCE_PER_SIGNAL: Final[int] = 2
"""PRD §2.1#1 strategy: '每个活跃信号的 evidence_event_ids 各取最多 2 条'."""

MAX_HIGHLIGHTS_PER_UNCOVERED_DOMAIN: Final[int] = 2
"""PRD §2.1#1 strategy: '其余事件按"未被信号 cover 的 domain"原则各取 1-2 条'."""

MAX_TOTAL_HIGHLIGHTS: Final[int] = 8
"""PRD §2.1#1: '≤ 8 条最值得保留的原文事件摘要'.
Soft cap — milestones bypass it (PRD §2.1#7 test gate is non-negotiable)."""

SIGNAL_ONE_LINER_BUDGET_CHARS: Final[int] = 120
"""PRD calls for ~60-char one-liners; we allow up to 120 to leave room for
Chinese punctuation + delta digits without truncating the model name."""

WEEK_LENGTH_DAYS: Final[int] = 7


class ContextCompressorError(RuntimeError):
    """Raised when input doesn't make sense (unknown child, non-Monday start, …)."""


# ---- output shapes (Pydantic, frozen) -------------------------------------


HighlightReason = Literal["milestone", "signal_evidence", "uncovered_domain"]


class SignalSummary(BaseModel):
    """One-line summary of an active signal, fed to the writer agent.

    Format target (≤ 120 chars):
        "<signal_type>@<primary_domain> i=0.NN [Δ±0.NN] — <notes>"
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: str = Field(min_length=1, max_length=64)
    one_liner: str = Field(min_length=1, max_length=SIGNAL_ONE_LINER_BUDGET_CHARS)


class EventHighlight(BaseModel):
    """Trimmed event — `raw_text` deliberately dropped (§2.1#1 strategy)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    timestamp: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    type: str = Field(min_length=1)
    domains: list[str] = Field(min_length=1)
    reason: HighlightReason


class DomainDelta(BaseModel):
    """Per-domain change vs the previous week.

    `delta` mirrors `compute_period_delta`'s contract:
      - None  → prior window had < PRIOR_SPARSE_THRESHOLD events
                (PRD: "no data" must NOT pretend to be "no change")
      - float ∈ [-1.0, +1.0]  → -1 silenced, +1 ≈ doubled
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str = Field(min_length=1)
    delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    current_event_count: int = Field(ge=0)
    prior_event_count: int = Field(ge=0)


class CompressedContext(BaseModel):
    """The full compressed snapshot the writer Agent will read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    child_id: str = Field(min_length=1, max_length=64)
    week_start: dt.date
    week_end: dt.date  # exclusive (next Monday)
    child_age_months: int = Field(ge=0, le=600)
    signals: list[SignalSummary]
    event_highlights: list[EventHighlight]
    period_deltas: list[DomainDelta]
    raw_token_count: int = Field(ge=0)


# ---- public API -----------------------------------------------------------


def compress_week_context(
    child_id: str,
    week_start: dt.date,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    conn: sqlite3.Connection | None = None,
) -> CompressedContext:
    """Build a compressed context for one (child, week).

    Parameters
    ----------
    child_id    : children.id; must exist
    week_start  : Monday (00:00 local). PRD §3.4 forbids ISO weeks; we want
                  the parent-friendly "本地周一→下周一" convention. Non-Monday
                  raises ContextCompressorError.
    max_tokens  : soft budget for the compressed payload. Logs a warning
                  when exceeded; does NOT mutate the output (the writer
                  layer decides how to react).
    conn        : optional caller-owned connection; we open one if absent.
    """
    own_conn = conn is None
    if conn is None:
        conn = db_module.get_conn()
    try:
        return _compress(conn, child_id, week_start, max_tokens)
    finally:
        if own_conn:
            conn.close()


def _compress(
    conn: sqlite3.Connection,
    child_id: str,
    week_start: dt.date,
    max_tokens: int,
) -> CompressedContext:
    if week_start.weekday() != 0:  # Monday=0 in Python
        raise ContextCompressorError(
            f"week_start must be a Monday, got {week_start.isoformat()} "
            f"({week_start.strftime('%A')})"
        )

    child_row = conn.execute(
        "SELECT id, birthday FROM children WHERE id = ?", (child_id,)
    ).fetchone()
    if child_row is None:
        raise ContextCompressorError(
            f"child_id={child_id!r} not found in `children`"
        )
    birthday_iso = str(child_row["birthday"])

    week_end = week_start + dt.timedelta(days=WEEK_LENGTH_DAYS)
    age_months = _age_at(birthday_iso, week_start)

    signals = _load_active_signals(conn, child_id, week_start, week_end)
    events = _load_week_events(conn, child_id, week_start, week_end)

    signal_summaries = [_summarize_signal(s) for s in signals]
    highlights = _select_highlights(events, signals)
    deltas = _compute_domain_deltas(
        conn, child_id, signals, events, week_start, week_end
    )

    # Build with placeholder token count, then re-emit with the real one.
    # (CompressedContext is frozen; model_copy gives us a clean rebuild.)
    draft = CompressedContext(
        child_id=child_id,
        week_start=week_start,
        week_end=week_end,
        child_age_months=age_months,
        signals=signal_summaries,
        event_highlights=highlights,
        period_deltas=deltas,
        raw_token_count=0,
    )
    token_count = _estimate_token_count(draft)
    if token_count > max_tokens:
        logger.warning(
            "context_compressor: %d est. tokens > budget %d "
            "(child=%s week=%s) — writer may downgrade or truncate",
            token_count,
            max_tokens,
            child_id,
            week_start.isoformat(),
        )
    return draft.model_copy(update={"raw_token_count": token_count})


# ---- DB I/O ---------------------------------------------------------------


def _load_active_signals(
    conn: sqlite3.Connection,
    child_id: str,
    week_start: dt.date,
    week_end: dt.date,
) -> list[Signal]:
    """Active signals whose last_seen_at falls inside the week."""
    rows = conn.execute(
        """
        SELECT id, child_id, signal_type, domains_json, intensity,
               child_age_months, delta_from_last_period, confidence,
               first_seen_at, last_seen_at, evidence_event_ids_json,
               status, notes
        FROM signals
        WHERE child_id = ?
          AND status = 'active'
          AND last_seen_at >= ?
          AND last_seen_at <  ?
        ORDER BY last_seen_at DESC
        """,
        (child_id, week_start.isoformat(), week_end.isoformat()),
    ).fetchall()
    return [Signal.from_row(dict(r)) for r in rows]


def _load_week_events(
    conn: sqlite3.Connection,
    child_id: str,
    week_start: dt.date,
    week_end: dt.date,
) -> list[dict[str, object]]:
    """All events in the week, ascending by timestamp."""
    rows = conn.execute(
        """
        SELECT id, timestamp, summary, type, domains_json
        FROM events
        WHERE child_id = ?
          AND timestamp >= ?
          AND timestamp <  ?
        ORDER BY timestamp ASC
        """,
        (child_id, week_start.isoformat(), week_end.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


# ---- compression steps ----------------------------------------------------


def _summarize_signal(sig: Signal) -> SignalSummary:
    """Render a signal as a compact one-liner.

    Format:
        <type>@<primary_domain> i=<intensity> [Δ<sign><delta>] [— <notes>]
    """
    domain = sig.domains[0] if sig.domains else "—"

    if sig.delta_from_last_period is None:
        delta_part = ""
    else:
        sign = "+" if sig.delta_from_last_period >= 0 else ""
        delta_part = f" Δ{sign}{sig.delta_from_last_period:.2f}"

    notes = (sig.notes or "").strip().replace("\n", " ")
    if len(notes) > 30:
        notes = notes[:29] + "…"
    notes_part = f" — {notes}" if notes else ""

    one_liner = (
        f"{sig.signal_type}@{domain} i={sig.intensity:.2f}"
        f"{delta_part}{notes_part}"
    )
    # Hard guard so the model_validate ceiling is never tripped at runtime.
    if len(one_liner) > SIGNAL_ONE_LINER_BUDGET_CHARS:
        one_liner = one_liner[: SIGNAL_ONE_LINER_BUDGET_CHARS - 1] + "…"
    return SignalSummary(signal_id=sig.id, one_liner=one_liner)


def _select_highlights(
    events: list[dict[str, object]],
    signals: list[Signal],
) -> list[EventHighlight]:
    """PRD-locked selection: milestones → signal evidence → uncovered domains.

    Milestones bypass MAX_TOTAL_HIGHLIGHTS — PRD §2.1#7 makes "all milestones
    survive compression" a non-negotiable test gate. The cap only trims
    discretionary picks (signal evidence + uncovered domain).
    """
    selected_ids: set[str] = set()
    out: list[EventHighlight] = []

    by_id: dict[str, dict[str, object]] = {str(e["id"]): e for e in events}

    # Step 1 — every milestone, no questions asked.
    for ev in events:
        if str(ev["type"]) == "milestone":
            out.append(_to_highlight(ev, "milestone"))
            selected_ids.add(str(ev["id"]))

    # Step 2 — up to 2 evidence events per active signal.
    for sig in signals:
        kept = 0
        for eid in sig.evidence_event_ids:
            if kept >= MAX_EVIDENCE_PER_SIGNAL:
                break
            if eid in selected_ids or eid not in by_id:
                continue
            out.append(_to_highlight(by_id[eid], "signal_evidence"))
            selected_ids.add(eid)
            kept += 1

    # Step 3 — domains not covered by any signal pick up 1-2 events each.
    covered: set[str] = set()
    for sig in signals:
        covered.update(sig.domains)

    bucket: dict[str, list[dict[str, object]]] = defaultdict(list)
    for ev in events:
        eid = str(ev["id"])
        if eid in selected_ids:
            continue
        domains = json.loads(str(ev.get("domains_json") or "[]"))
        for d in domains:
            if d in covered:
                continue
            bucket[d].append(ev)
            break  # one event placed under at most one uncovered-domain bucket

    for _domain, candidates in bucket.items():
        for ev in candidates[:MAX_HIGHLIGHTS_PER_UNCOVERED_DOMAIN]:
            eid = str(ev["id"])
            if eid in selected_ids:
                continue
            out.append(_to_highlight(ev, "uncovered_domain"))
            selected_ids.add(eid)

    # Trim — protect milestones, sort discretionary picks by reason then time.
    if len(out) > MAX_TOTAL_HIGHLIGHTS:
        milestones = [h for h in out if h.reason == "milestone"]
        others = [h for h in out if h.reason != "milestone"]
        priority: dict[HighlightReason, int] = {
            "milestone": 0,
            "signal_evidence": 1,
            "uncovered_domain": 2,
        }
        others.sort(key=lambda h: (priority[h.reason], h.timestamp))
        budget = MAX_TOTAL_HIGHLIGHTS - len(milestones)
        out = milestones + (others[:budget] if budget > 0 else [])

    out.sort(key=lambda h: h.timestamp)
    return out


def _to_highlight(event: dict[str, object], reason: HighlightReason) -> EventHighlight:
    domains = json.loads(str(event.get("domains_json") or "[]"))
    return EventHighlight(
        event_id=str(event["id"]),
        timestamp=str(event["timestamp"]),
        summary=str(event["summary"]),
        type=str(event["type"]),
        domains=list(domains) if domains else ["other"],
        reason=reason,
    )


def _compute_domain_deltas(
    conn: sqlite3.Connection,
    child_id: str,
    signals: list[Signal],
    events: list[dict[str, object]],
    week_start: dt.date,
    week_end: dt.date,
) -> list[DomainDelta]:
    """For every active domain (signal or event mention), emit a delta row.

    Reuses Phase 1's `compute_period_delta` so the math stays consistent
    with the heatmap and signal_extractor's own delta wiring.
    """
    active_domains: set[str] = set()
    for sig in signals:
        active_domains.update(sig.domains)
    for ev in events:
        active_domains.update(json.loads(str(ev.get("domains_json") or "[]")))

    if not active_domains:
        return []

    prior_start = week_start - dt.timedelta(days=WEEK_LENGTH_DAYS)
    prior_end = week_start  # exclusive
    # compute_period_delta's _window_bounds adds +1 day to `end`, so we
    # pass the LAST INCLUDED day (week_end - 1 day, prior_end - 1 day).
    cur_window: tuple[dt.date, dt.date] = (
        week_start,
        week_end - dt.timedelta(days=1),
    )
    prior_window: tuple[dt.date, dt.date] = (
        prior_start,
        prior_end - dt.timedelta(days=1),
    )

    # Pre-fetch prior-week events ONCE for counting; keeps the per-domain
    # loop cheap (no N+1 queries).
    prior_rows = conn.execute(
        """
        SELECT domains_json FROM events
        WHERE child_id = ?
          AND timestamp >= ?
          AND timestamp <  ?
        """,
        (child_id, prior_start.isoformat(), prior_end.isoformat()),
    ).fetchall()
    prior_domain_lists = [
        json.loads(str(r["domains_json"] or "[]")) for r in prior_rows
    ]
    cur_domain_lists = [
        json.loads(str(ev.get("domains_json") or "[]")) for ev in events
    ]

    out: list[DomainDelta] = []
    for domain in sorted(active_domains):
        delta = compute_period_delta(
            child_id,
            domain,
            cur_window,
            prior_window,
            conn=conn,
        )
        cur_count = sum(1 for ds in cur_domain_lists if domain in ds)
        prior_count = sum(1 for ds in prior_domain_lists if domain in ds)
        out.append(
            DomainDelta(
                domain=domain,
                delta=delta,
                current_event_count=cur_count,
                prior_event_count=prior_count,
            )
        )
    return out


# ---- helpers --------------------------------------------------------------


def _age_at(birthday_iso: str, week_start: dt.date) -> int:
    """Age-months frozen at week_start (PRD: child_age_months 不漂移).

    compute_age_months wants an ISO 8601 timestamp; we anchor to local
    midnight of week_start (timezone +08:00, the PRD §3.4 baseline).
    """
    return compute_age_months(
        birthday_iso, f"{week_start.isoformat()}T00:00:00+08:00"
    )


def _estimate_token_count(ctx: CompressedContext) -> int:
    """Cheap token estimator for cost logs.

    Heuristic: Chinese char ≈ 1 token, ASCII chunk ≈ 4 chars/token. This
    is intentionally over-counting Chinese (cl100k_base usually compresses
    Chinese ~1.5 chars/token) so the budget gate stays conservative. Good
    enough for §2.1#7's "≤ 4k tokens" assertion — we don't need tiktoken.
    """
    payload = ctx.model_dump_json()
    chinese = sum(1 for c in payload if "一" <= c <= "鿿")
    other = len(payload) - chinese
    return chinese + (other + 3) // 4
