"""Signal Extractor Agent v0.

Phase 1 / PRD prd/phase1-signals.md §2.1 #2.

Two-layer pipeline:
  1) Rule layer (cheap, deterministic): scan an event window, propose
     candidate signals. No LLM call.
  2) LLM layer (per-candidate): show the candidate + its evidence to a
     local small model and ask "is this a real pattern?". Output is a
     {accept, intensity, confidence, notes} JSON. Closed-set validated.

Why this split:
  - The rule layer keeps cost predictable (PRD §3: still $0/month).
  - The LLM layer adds judgement we can't easily encode (e.g. "two
    'fell down' events in the same week — is that a motor-skill leap or
    just clumsy wednesdays?"). Without it we'd either over-fire or
    miss everything that doesn't fit a hand-crafted rule.

We deliberately do NOT call the LLM unless the rule layer found a
candidate first. This keeps backfill of 100 events cheap.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from src.core import db as db_module
from src.core.llm_client import LLMClient, LLMError, parse_json_strict
from src.core.models import (
    ALLOWED_SIGNAL_TYPES,
    Signal,
    SignalType,
    compute_age_months,
    new_signal_id,
)
from src.core.signal_delta import compute_period_delta

logger = logging.getLogger(__name__)

PROMPT_PATH: Final[Path] = (
    Path(__file__).parent.parent / "prompts" / "signal_extractor.md"
)

# ---- rule layer config (PRD §2.1#2) ----------------------------------------

DEFAULT_WINDOW_DAYS: Final[int] = 14
INTEREST_MIN_COUNT: Final[int] = 3        # ≥3 same-domain events → interest_pattern
EMOTION_MIN_DAYS: Final[int] = 3          # same emotion in same context ≥ 3 days
MIN_EVIDENCE: Final[int] = 2              # PRD: <2 doesn't form a signal
ANOMALY_PRIOR_MIN: Final[int] = 4         # prior window must have been active enough
ANOMALY_DROP_RATIO: Final[float] = 0.3    # current ≤ 30% of prior → anomaly candidate


class SignalExtractorError(RuntimeError):
    """Raised when extraction fails irrecoverably."""


# ---- shapes ----------------------------------------------------------------


@dataclass(frozen=True)
class EventLite:
    """Trimmed view of a row from `events` for the rule layer.

    Carries only what the rule + LLM layer need; keeps the rule layer
    cheap to test (no DB needed).
    """

    id: str
    timestamp: str          # ISO 8601
    summary: str
    type: str               # milestone | observation | routine | concern | other
    domains: list[str]
    emotions: list[str]
    context: str


@dataclass(frozen=True)
class CandidateSignal:
    """Output of the rule layer; input to the LLM layer."""

    signal_type: SignalType
    domains: list[str]
    evidence: list[EventLite]
    # Rule-layer pre-estimate (the LLM may overrule):
    rule_intensity_hint: float


@dataclass(frozen=True)
class LLMVerdict:
    accept: bool
    intensity: float
    confidence: float
    notes: str


# ---- rule layer ------------------------------------------------------------


def propose_candidates(
    events: Sequence[EventLite],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now_iso: str | None = None,
) -> list[CandidateSignal]:
    """Scan a window of events and propose candidate signals.

    Rules:
      R1. ≥ INTEREST_MIN_COUNT events sharing a non-trivial domain → interest_pattern.
      R2. type=milestone single event → growth_leap candidate (LLM decides).
      R3. same emotion + same context across ≥ EMOTION_MIN_DAYS distinct days
          → emotion_pattern.

    Anomaly + skill_pattern are richer judgements — punted to the LLM layer
    in v0 (it can flag accept=true on what we surface as interest/emotion;
    we'll learn from real data which extra rules are worth coding).

    `events` may include items outside the window; we filter here.
    """
    now = _parse_iso(now_iso) if now_iso else _now_local()
    cutoff = now - dt.timedelta(days=window_days)
    prior_cutoff = cutoff - dt.timedelta(days=window_days)
    in_window = [e for e in events if _parse_iso(e.timestamp) >= cutoff]
    prior_window = [
        e
        for e in events
        if prior_cutoff <= _parse_iso(e.timestamp) < cutoff
    ]

    candidates: list[CandidateSignal] = []
    candidates.extend(_rule_interest(in_window))
    candidates.extend(_rule_growth_leap(in_window))
    candidates.extend(_rule_emotion(in_window))
    candidates.extend(_rule_anomaly(in_window, prior_window))
    return candidates


def _rule_interest(events: Sequence[EventLite]) -> list[CandidateSignal]:
    """R1: ≥3 events sharing a domain (excluding 'other' / 'routine' /
    'self_care' which are too coarse to count as "interest")."""
    coarse = {"other", "routine", "self_care"}
    by_domain: dict[str, list[EventLite]] = defaultdict(list)
    for e in events:
        for d in e.domains:
            if d in coarse:
                continue
            by_domain[d].append(e)

    out: list[CandidateSignal] = []
    for domain, evs in by_domain.items():
        if len(evs) < INTEREST_MIN_COUNT:
            continue
        evs_sorted = sorted(evs, key=lambda x: x.timestamp)
        # rule-layer intensity hint: how saturated the window is, capped at 1
        hint = min(len(evs_sorted) / 6.0, 1.0)
        out.append(
            CandidateSignal(
                signal_type="interest_pattern",
                domains=[domain],
                evidence=evs_sorted,
                rule_intensity_hint=hint,
            )
        )
    return out


def _rule_growth_leap(events: Sequence[EventLite]) -> list[CandidateSignal]:
    """R2: each milestone event yields one growth_leap candidate.

    PRD allows MIN_EVIDENCE=2 for signals; for growth_leap we relax it: a
    single milestone IS the leap. We enrich evidence by attaching ≤2
    nearest-in-time same-domain events to give the LLM context. If
    enrichment fails to produce ≥2 evidence items, we drop the candidate
    (the schema-level constraint stays).
    """
    out: list[CandidateSignal] = []
    for ms in events:
        if ms.type != "milestone":
            continue
        related = [
            e for e in events
            if e.id != ms.id and any(d in ms.domains for d in e.domains)
        ]
        related_sorted = sorted(
            related,
            key=lambda x: abs(
                (_parse_iso(x.timestamp) - _parse_iso(ms.timestamp)).total_seconds()
            ),
        )[:2]
        evidence = [ms, *related_sorted]
        if len(evidence) < MIN_EVIDENCE:
            continue
        out.append(
            CandidateSignal(
                signal_type="growth_leap",
                domains=list(ms.domains[:1] or ["other"]),
                evidence=evidence,
                rule_intensity_hint=0.8,  # milestone = inherently strong
            )
        )
    return out


def _rule_emotion(events: Sequence[EventLite]) -> list[CandidateSignal]:
    """R3: same emotion + same coarse context across ≥3 distinct days."""
    by_key: dict[tuple[str, str], list[EventLite]] = defaultdict(list)
    for e in events:
        ctx = (e.context or "").strip().lower()
        for emo in e.emotions:
            by_key[(emo, ctx)].append(e)

    out: list[CandidateSignal] = []
    for (emo, _ctx), evs in by_key.items():
        distinct_days = {e.timestamp[:10] for e in evs}
        if len(distinct_days) < EMOTION_MIN_DAYS:
            continue
        evs_sorted = sorted(evs, key=lambda x: x.timestamp)
        # collapse domains across the bucket
        seen_domains: list[str] = []
        for e in evs_sorted:
            for d in e.domains:
                if d not in seen_domains:
                    seen_domains.append(d)
        out.append(
            CandidateSignal(
                signal_type="emotion_pattern",
                domains=seen_domains[:3] or ["emotion"],
                evidence=evs_sorted,
                rule_intensity_hint=min(len(distinct_days) / 5.0, 1.0),
            )
        )
        # tag the emotion in notes via extending domains? — no, keep clean;
        # the LLM gets emotions inside each evidence row anyway.
        _ = emo
    return out


def _rule_anomaly(
    current: Sequence[EventLite], prior: Sequence[EventLite]
) -> list[CandidateSignal]:
    """R4 (anomaly): a previously-active domain has gone quiet.

    Triggers when:
      - prior window had ≥ ANOMALY_PRIOR_MIN events in a non-coarse domain
      - current window has ≤ ANOMALY_DROP_RATIO × prior count
    Surfaces ≤2 prior events as evidence (the LLM judge can decide if it's
    a real withdrawal or just a busy week).

    Coarse domains (other/routine/self_care) are excluded — drops in
    bedtime routine usually mean "we changed the schedule", not anomaly.
    """
    coarse = {"other", "routine", "self_care"}

    def by_domain(events: Sequence[EventLite]) -> dict[str, list[EventLite]]:
        out: dict[str, list[EventLite]] = defaultdict(list)
        for e in events:
            for d in e.domains:
                if d in coarse:
                    continue
                out[d].append(e)
        return out

    prior_by = by_domain(prior)
    current_by = by_domain(current)

    candidates: list[CandidateSignal] = []
    for domain, prior_evs in prior_by.items():
        if len(prior_evs) < ANOMALY_PRIOR_MIN:
            continue
        cur_count = len(current_by.get(domain, []))
        if cur_count > ANOMALY_DROP_RATIO * len(prior_evs):
            continue
        # surface up-to-2 most recent prior events as evidence
        prior_sorted = sorted(prior_evs, key=lambda x: x.timestamp, reverse=True)[:2]
        if len(prior_sorted) < MIN_EVIDENCE:
            continue
        # rule-layer hint: how stark the drop is, in [0, 1]
        hint = 1.0 - (cur_count / max(len(prior_evs), 1))
        candidates.append(
            CandidateSignal(
                signal_type="anomaly",
                domains=[domain],
                evidence=prior_sorted,
                rule_intensity_hint=min(hint, 1.0),
            )
        )
    return candidates


# ---- LLM layer -------------------------------------------------------------


def _load_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise SignalExtractorError(f"prompt missing at {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _candidate_to_llm_input(c: CandidateSignal) -> str:
    return json.dumps(
        {
            "signal_type": c.signal_type,
            "domains": c.domains,
            "evidence": [
                {
                    "summary": e.summary,
                    "type": e.type,
                    "domains": e.domains,
                    "emotions": e.emotions,
                    "context": e.context,
                    "timestamp": e.timestamp,
                }
                for e in c.evidence
            ],
        },
        ensure_ascii=False,
    )


def _parse_verdict(raw: dict[str, object]) -> LLMVerdict:
    accept = raw.get("accept")
    if not isinstance(accept, bool):
        raise SignalExtractorError(f"`accept` must be bool, got {accept!r}")

    intensity = raw.get("intensity")
    if not isinstance(intensity, (int, float)) or not 0.0 <= float(intensity) <= 1.0:
        raise SignalExtractorError(
            f"`intensity` must be 0.0-1.0, got {intensity!r}"
        )

    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise SignalExtractorError(
            f"`confidence` must be 0.0-1.0, got {confidence!r}"
        )

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise SignalExtractorError(f"`notes` must be string, got {notes!r}")

    return LLMVerdict(
        accept=accept,
        intensity=float(intensity),
        confidence=float(confidence),
        notes=notes.strip(),
    )


# ---- top-level orchestration ----------------------------------------------


class SignalExtractor:
    """Pipeline: load events → rules → LLM judgement → write to signals."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()
        self._system = _load_prompt()

    # --- exposed for tests --------------------------------------------------

    def judge(self, candidate: CandidateSignal) -> LLMVerdict:
        """Run a single candidate through the LLM layer."""
        try:
            result = self._llm.generate(
                prompt=_candidate_to_llm_input(candidate),
                system=self._system,
                purpose="signal",
                json_mode=True,
            )
        except LLMError as e:
            raise SignalExtractorError(str(e)) from e

        try:
            payload = parse_json_strict(result.text)
        except LLMError as e:
            raise SignalExtractorError(str(e)) from e
        return _parse_verdict(payload)

    # --- LLM-vs-rule disagreement logging (Phase 2 prep) -------------------

    def _log_disagreement(
        self, cand: CandidateSignal, verdict: LLMVerdict
    ) -> None:
        """Log when the LLM disagrees with the rule-layer prior.

        We define disagreement as either:
          - reject: LLM said `accept=False` despite the rule firing
          - large_intensity_drift: |verdict.intensity - rule_hint| ≥ 0.3
                                    (chosen on the dimension we'd most likely
                                     act on; tightening once we have a real
                                     reviewer pass in Phase 2)

        This is log-only — no DB writes, no API surface change. ADR-0001 F7
        wants this to inform the 3B → 7B upgrade decision; phase1-baseline.md §6
        flagged it as the missing piece. We start with prints so a quick
        `grep` on logs gives us a tally, and we'll graduate to a structured
        store when Phase 2 needs a UI for it.
        """
        intensity_drift = abs(verdict.intensity - cand.rule_intensity_hint)
        if not verdict.accept:
            logger.info(
                "signal.disagreement reject signal_type=%s domain=%s "
                "rule_hint=%.2f llm_confidence=%.2f notes=%r",
                cand.signal_type,
                cand.domains[0] if cand.domains else "",
                cand.rule_intensity_hint,
                verdict.confidence,
                verdict.notes[:80],
            )
            return
        if intensity_drift >= 0.3:
            logger.info(
                "signal.disagreement intensity_drift=%.2f signal_type=%s "
                "domain=%s rule_hint=%.2f llm_intensity=%.2f confidence=%.2f",
                intensity_drift,
                cand.signal_type,
                cand.domains[0] if cand.domains else "",
                cand.rule_intensity_hint,
                verdict.intensity,
                verdict.confidence,
            )

    # --- main entrypoint ----------------------------------------------------

    def extract_for_child(
        self,
        *,
        child_id: str,
        window_days: int = DEFAULT_WINDOW_DAYS,
        now_iso: str | None = None,
    ) -> list[Signal]:
        """Read events for child, propose, judge, persist accepted signals.

        Returns the list of Signals that were accepted + written to DB.
        """
        conn = db_module.get_conn()
        try:
            child_row = conn.execute(
                "SELECT id, birthday FROM children WHERE id = ?", (child_id,)
            ).fetchone()
            if child_row is None:
                raise SignalExtractorError(f"child_id={child_id!r} not found")
            birthday = str(child_row["birthday"])

            events = _load_events(conn, child_id)
            candidates = propose_candidates(
                events, window_days=window_days, now_iso=now_iso
            )
            if not candidates:
                return []

            accepted: list[Signal] = []
            now = now_iso or _now_local_iso()
            for idx, cand in enumerate(candidates, start=1):
                try:
                    verdict = self.judge(cand)
                except SignalExtractorError as e:
                    logger.warning("LLM verdict failed: %s — skipping candidate", e)
                    continue
                self._log_disagreement(cand, verdict)
                if not verdict.accept:
                    continue
                delta = _compute_signal_delta(
                    conn=conn,
                    child_id=child_id,
                    cand=cand,
                    now_iso=now,
                    window_days=window_days,
                )
                signal = _build_signal(
                    cand=cand,
                    verdict=verdict,
                    child_id=child_id,
                    birthday=birthday,
                    seq=idx,
                    now_iso=now,
                    delta=delta,
                )
                _insert_signal(conn, signal)
                accepted.append(signal)
            return accepted
        finally:
            conn.close()


# ---- helpers --------------------------------------------------------------


def _load_events(conn: sqlite3.Connection, child_id: str) -> list[EventLite]:
    rows = conn.execute(
        """
        SELECT id, timestamp, summary, type, domains_json, emotions_json, context
        FROM events WHERE child_id = ? ORDER BY timestamp ASC
        """,
        (child_id,),
    ).fetchall()
    out: list[EventLite] = []
    for r in rows:
        out.append(
            EventLite(
                id=r["id"],
                timestamp=r["timestamp"],
                summary=r["summary"],
                type=r["type"],
                domains=json.loads(r["domains_json"] or "[]"),
                emotions=json.loads(r["emotions_json"] or "[]"),
                context=r["context"] or "",
            )
        )
    return out


def _build_signal(
    *,
    cand: CandidateSignal,
    verdict: LLMVerdict,
    child_id: str,
    birthday: str,
    seq: int,
    now_iso: str,
    delta: float | None = None,
) -> Signal:
    first_seen = cand.evidence[0].timestamp
    last_seen = cand.evidence[-1].timestamp
    age = compute_age_months(birthday, last_seen)
    sig_id = new_signal_id(now_iso, seq)

    if cand.signal_type not in ALLOWED_SIGNAL_TYPES:
        # defence in depth — proposers should never produce an unknown type
        raise SignalExtractorError(f"unknown signal_type {cand.signal_type!r}")

    return Signal(
        id=sig_id,
        child_id=child_id,
        signal_type=cand.signal_type,
        domains=cand.domains,
        intensity=verdict.intensity,
        child_age_months=age,
        delta_from_last_period=delta,
        confidence=verdict.confidence,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        evidence_event_ids=[e.id for e in cand.evidence],
        status="active",
        notes=verdict.notes or f"rule_hint={cand.rule_intensity_hint:.2f}",
    )


def _compute_signal_delta(
    *,
    conn: sqlite3.Connection,
    child_id: str,
    cand: CandidateSignal,
    now_iso: str,
    window_days: int,
) -> float | None:
    """Compute the per-domain change vs. the prior window.

    Policy:
      - emotion_pattern: skipped — its `domains` field carries event domains
        like ["music", "reading"] aggregated across an emotion bucket, so a
        domain-counting delta isn't meaningful (we'd be measuring "did the
        child do music this week vs. last", not "did the child feel proud
        in this context"). Phase 2 will revisit emotional deltas with a
        dedicated metric.
      - everything else: take the FIRST listed domain (rule layer always
        puts the primary domain first — see _rule_interest, _rule_growth_leap,
        _rule_anomaly).

    Returns None when:
      - signal type is emotion_pattern (above)
      - prior window has fewer than PRIOR_SPARSE_THRESHOLD events
        (compute_period_delta enforces this — None = "no baseline yet")
    """
    if cand.signal_type == "emotion_pattern":
        return None
    if not cand.domains:
        return None
    primary_domain = cand.domains[0]

    now_dt = _parse_iso(now_iso)
    cutoff = now_dt - dt.timedelta(days=window_days)
    prior_cutoff = cutoff - dt.timedelta(days=window_days)
    current_window = (cutoff.date(), now_dt.date())
    prior_window = (prior_cutoff.date(), cutoff.date())

    return compute_period_delta(
        child_id,
        primary_domain,
        current_window,
        prior_window,
        conn=conn,
    )


def _insert_signal(conn: sqlite3.Connection, signal: Signal) -> None:
    row = signal.as_row()
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    with db_module.transactional(conn):
        conn.execute(
            f"INSERT INTO signals ({cols}) VALUES ({placeholders})",
            row,
        )


def _parse_iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def _now_local() -> dt.datetime:
    return dt.datetime.now(dt.UTC).astimezone()


def _now_local_iso() -> str:
    return _now_local().isoformat(timespec="seconds")


