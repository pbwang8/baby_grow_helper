"""Phase 1 M1.3 — period delta + heatmap aggregation.

PRD `prd/phase1-signals.md` §2.1#4:
  Compare a child's recent window vs the previous window, per-domain,
  and emit a `[-1.0, +1.0]` change score (or `None` when prior window is
  too sparse to be meaningful).

Why None matters:
  In Phase 1, "no data" must NOT pretend to be "no change". Returning 0.0
  for an empty prior window would silently hide the fact that we don't
  yet have a baseline — and the heatmap would render that as "stable",
  which is the wrong story.

Heatmap also lives here because it's the same aggregation under a
different group-by: count + intensity-weight per (domain, age_month).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Final

from src.core import db as db_module
from src.core.models import compute_age_months

# Below this many events, the prior window can't tell us "stable" from
# "no data" — the PRD explicitly forbids conflating those.
PRIOR_SPARSE_THRESHOLD: Final[int] = 3


# ---- delta -----------------------------------------------------------------


def compute_period_delta(
    child_id: str,
    domain: str,
    current_window: tuple[dt.date, dt.date],
    prior_window: tuple[dt.date, dt.date],
    *,
    conn: sqlite3.Connection | None = None,
) -> float | None:
    """Return the change in `domain` activity between two windows.

    Output range:
      -1.0  → domain went silent (was N, now 0)
      0.0   → unchanged (within noise)
      +1.0  → roughly doubled or more

    We use signal `intensity` as a weight: if the LLM scored a recent
    pattern at 0.9, that counts more than a single 0.3-intensity blip.
    Falls back to event counts when signals haven't been extracted yet.

    Returns `None` (NOT 0.0) when the prior window has fewer than
    `PRIOR_SPARSE_THRESHOLD` events — we don't have a baseline.
    """
    own_conn = conn is None
    if conn is None:
        conn = db_module.get_conn()
    try:
        prior_score = _window_score(conn, child_id, domain, prior_window)
        if prior_score.event_count < PRIOR_SPARSE_THRESHOLD:
            return None
        current_score = _window_score(conn, child_id, domain, current_window)
        return _normalize_delta(current_score.weighted, prior_score.weighted)
    finally:
        if own_conn:
            conn.close()


@dataclass(frozen=True)
class _WindowScore:
    event_count: int
    weighted: float  # sum of (intensity proxy) over events in window


def _window_score(
    conn: sqlite3.Connection,
    child_id: str,
    domain: str,
    window: tuple[dt.date, dt.date],
) -> _WindowScore:
    """Sum intensity-weighted events for one (child, domain, window).

    We pull events directly (not signals) because:
      - Signals fire only on patterns of ≥ 3 events; counting only them
        would underweight the actual activity volume.
      - Each event contributes 1.0 baseline; we then **boost** events
        that are evidence for an `active` signal by that signal's
        intensity. Equivalent to "if we already have a story for this
        period, weight evidence more heavily".
    """
    start, end = _window_bounds(window)
    rows = conn.execute(
        """
        SELECT id, domains_json
        FROM events
        WHERE child_id = ?
          AND timestamp >= ?
          AND timestamp <  ?
        """,
        (child_id, start, end),
    ).fetchall()

    matching_event_ids: list[str] = []
    for r in rows:
        domains = json.loads(r["domains_json"] or "[]")
        if domain in domains:
            matching_event_ids.append(r["id"])

    if not matching_event_ids:
        return _WindowScore(event_count=0, weighted=0.0)

    # Boost events that show up in any active signal for the same
    # (child, domain).
    boosts = _evidence_boosts(conn, child_id, domain, matching_event_ids)
    weighted = sum(1.0 + boosts.get(eid, 0.0) for eid in matching_event_ids)
    return _WindowScore(event_count=len(matching_event_ids), weighted=weighted)


def _evidence_boosts(
    conn: sqlite3.Connection,
    child_id: str,
    domain: str,
    event_ids: list[str],
) -> dict[str, float]:
    """For each event_id that is evidence for an active signal in this
    domain, return that signal's intensity. Multiple signals → max."""
    rows = conn.execute(
        """
        SELECT intensity, evidence_event_ids_json, domains_json
        FROM signals
        WHERE child_id = ? AND status = 'active'
        """,
        (child_id,),
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        domains = json.loads(r["domains_json"] or "[]")
        if domain not in domains:
            continue
        evidence = json.loads(r["evidence_event_ids_json"] or "[]")
        intensity = float(r["intensity"])
        for eid in evidence:
            if eid in event_ids and intensity > out.get(eid, 0.0):
                out[eid] = intensity
    return out


def _normalize_delta(current: float, prior: float) -> float:
    """Map two non-negative scores to a `[-1.0, +1.0]` change.

    Using the "ratio about midpoint" form: (c - p) / max(c, p).
    Easy to interpret:
      c == 0, p > 0  → -1.0  (silenced)
      c == p          →  0.0
      c == 2p         → +0.5
      c >> p          →  +1.0 in the limit
    """
    denom = max(current, prior)
    if denom <= 0.0:
        return 0.0
    raw = (current - prior) / denom
    # numerical safety: clamp to closed interval
    return max(-1.0, min(1.0, raw))


# ---- heatmap aggregation --------------------------------------------------


@dataclass(frozen=True)
class HeatmapCell:
    """One cell of the heatmap — child_age_months × domain.

    `intensity` is normalized to [0, 1] across the whole returned grid,
    so the front-end can render colours without knowing the global max.
    """

    age_months: int
    domain: str
    raw_score: float
    intensity: float
    event_count: int


def heatmap_data(
    child_id: str,
    *,
    domains: list[str] | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[HeatmapCell]:
    """Build the data for `/heatmap`.

    Per PRD §2.1#5: X axis is **child_age_months**, not calendar date.
    This is the function that enforces it: every event is bucketed by
    the child's age at the time the event happened.

    `domains`, if given, restricts the output. Otherwise all domains
    seen in the child's events are returned.
    """
    own_conn = conn is None
    if conn is None:
        conn = db_module.get_conn()
    try:
        child = conn.execute(
            "SELECT birthday FROM children WHERE id = ?", (child_id,)
        ).fetchone()
        if child is None:
            return []
        birthday = str(child["birthday"])

        rows = conn.execute(
            """
            SELECT id, timestamp, domains_json
            FROM events
            WHERE child_id = ?
            ORDER BY timestamp ASC
            """,
            (child_id,),
        ).fetchall()

        # gather all event ids per (age_months, domain)
        bucket_events: dict[tuple[int, str], list[str]] = defaultdict(list)
        for r in rows:
            ts = str(r["timestamp"])
            age = compute_age_months(birthday, ts)
            evt_domains = json.loads(r["domains_json"] or "[]")
            for d in evt_domains:
                if domains is not None and d not in domains:
                    continue
                bucket_events[(age, d)].append(str(r["id"]))

        if not bucket_events:
            return []

        # signal-intensity boost (re-using the same notion as period delta)
        signal_rows = conn.execute(
            """
            SELECT intensity, evidence_event_ids_json, domains_json
            FROM signals
            WHERE child_id = ? AND status = 'active'
            """,
            (child_id,),
        ).fetchall()
        boost_by_event_domain: dict[tuple[str, str], float] = {}
        for r in signal_rows:
            sd = json.loads(r["domains_json"] or "[]")
            evidence = json.loads(r["evidence_event_ids_json"] or "[]")
            intensity = float(r["intensity"])
            for d in sd:
                for eid in evidence:
                    key = (eid, d)
                    if intensity > boost_by_event_domain.get(key, 0.0):
                        boost_by_event_domain[key] = intensity
    finally:
        if own_conn:
            conn.close()

    raw_scores: dict[tuple[int, str], float] = {}
    for (age, d), eids in bucket_events.items():
        score = sum(1.0 + boost_by_event_domain.get((eid, d), 0.0) for eid in eids)
        raw_scores[(age, d)] = score

    max_score = max(raw_scores.values())
    cells = [
        HeatmapCell(
            age_months=age,
            domain=d,
            raw_score=score,
            intensity=score / max_score if max_score > 0 else 0.0,
            event_count=len(bucket_events[(age, d)]),
        )
        for (age, d), score in raw_scores.items()
    ]
    cells.sort(key=lambda c: (c.age_months, c.domain))
    return cells


# ---- helpers --------------------------------------------------------------


def _window_bounds(window: tuple[dt.date, dt.date]) -> tuple[str, str]:
    """Return (start_iso, end_iso) for a half-open [start, end) interval.

    We accept dates and treat `end` as exclusive (the day after the last
    day we want to include, conceptually). Caller passes in (start, end)
    where end is the day AFTER the last day, so a 14-day window ending
    today is `(today - 14, today + 1)` ... or actually, since our event
    timestamps are always on or after 00:00:00 of the date, we just
    compare against `start.isoformat()` (inclusive) and `(end+1d).isoformat()`
    (exclusive). Using ISO date strings on ISO-prefixed timestamps is
    safe because our timestamps are zero-padded.
    """
    start, end = window
    # half-open: include the start day, exclude the end day's midnight
    end_excl = end + dt.timedelta(days=1)
    return start.isoformat(), end_excl.isoformat()
