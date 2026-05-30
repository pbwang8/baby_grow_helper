"""Unit tests for the Signal Extractor.

Three layers tested separately:
  1) propose_candidates(): pure-function rule layer, no LLM, no DB.
  2) SignalExtractor.judge(): the LLM layer, with a stub LLMClient that
     returns canned JSON.
  3) SignalExtractor.extract_for_child(): full pipeline against a tmp DB.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from src.agents.signal_extractor import (
    CandidateSignal,
    EventLite,
    SignalExtractor,
    SignalExtractorError,
    propose_candidates,
)
from src.core import db as db_module
from src.core.llm_client import LLMClient, LLMResult
from src.core.models import Signal

# ---- helpers ---------------------------------------------------------------


def _ev(
    id_: str,
    ts: str,
    *,
    type_: str = "observation",
    summary: str = "x",
    domains: list[str] | None = None,
    emotions: list[str] | None = None,
    context: str = "",
) -> EventLite:
    return EventLite(
        id=id_,
        timestamp=ts,
        summary=summary,
        type=type_,
        domains=domains or ["language"],
        emotions=emotions or [],
        context=context,
    )


# ---- rule layer -----------------------------------------------------------


def test_rule_interest_pattern_fires_on_three_same_domain() -> None:
    events = [
        _ev("e1", "2026-05-15T10:00:00+08:00", domains=["music"]),
        _ev("e2", "2026-05-17T11:00:00+08:00", domains=["music"]),
        _ev("e3", "2026-05-19T12:00:00+08:00", domains=["music"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    interest = [c for c in cands if c.signal_type == "interest_pattern"]
    assert len(interest) == 1
    assert interest[0].domains == ["music"]
    assert len(interest[0].evidence) == 3


def test_rule_interest_pattern_does_not_fire_on_two() -> None:
    events = [
        _ev("e1", "2026-05-15T10:00:00+08:00", domains=["music"]),
        _ev("e2", "2026-05-19T12:00:00+08:00", domains=["music"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    assert all(c.signal_type != "interest_pattern" for c in cands)


def test_rule_interest_excludes_coarse_domains() -> None:
    """`other`, `routine`, `self_care` are too generic to count as 'interest'."""
    events = [
        _ev("e1", "2026-05-15T10:00:00+08:00", domains=["routine"]),
        _ev("e2", "2026-05-17T11:00:00+08:00", domains=["routine"]),
        _ev("e3", "2026-05-19T12:00:00+08:00", domains=["routine"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    assert [c for c in cands if c.signal_type == "interest_pattern"] == []


def test_rule_window_filters_old_events() -> None:
    events = [
        _ev("old1", "2026-04-01T10:00:00+08:00", domains=["music"]),
        _ev("old2", "2026-04-05T10:00:00+08:00", domains=["music"]),
        _ev("old3", "2026-04-10T10:00:00+08:00", domains=["music"]),
        _ev("new", "2026-05-19T10:00:00+08:00", domains=["music"]),
    ]
    cands = propose_candidates(
        events, window_days=14, now_iso="2026-05-23T20:00:00+08:00"
    )
    # only one in-window event ⇒ no interest_pattern
    assert [c for c in cands if c.signal_type == "interest_pattern"] == []


def test_rule_growth_leap_on_milestone_with_context() -> None:
    events = [
        _ev("ms", "2026-05-19T10:00:00+08:00", type_="milestone", domains=["motor"]),
        _ev("near1", "2026-05-17T10:00:00+08:00", domains=["motor"]),
        _ev("near2", "2026-05-15T10:00:00+08:00", domains=["motor"]),
        _ev("far", "2026-05-10T10:00:00+08:00", domains=["language"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    leap = [c for c in cands if c.signal_type == "growth_leap"]
    assert len(leap) == 1
    assert leap[0].evidence[0].id == "ms"
    # related same-domain events were attached
    assert any(e.id == "near1" for e in leap[0].evidence)
    # far cross-domain event was NOT attached
    assert all(e.id != "far" for e in leap[0].evidence)


def test_rule_growth_leap_dropped_when_no_related_events() -> None:
    events = [
        _ev("ms", "2026-05-19T10:00:00+08:00", type_="milestone", domains=["motor"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    # MIN_EVIDENCE=2 — single milestone with nothing nearby is dropped
    assert [c for c in cands if c.signal_type == "growth_leap"] == []


def test_rule_emotion_pattern_three_distinct_days() -> None:
    events = [
        _ev("e1", "2026-05-15T10:00:00+08:00", emotions=["frustrated"], context="餐桌"),
        _ev("e2", "2026-05-17T18:00:00+08:00", emotions=["frustrated"], context="餐桌"),
        _ev("e3", "2026-05-19T12:00:00+08:00", emotions=["frustrated"], context="餐桌"),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    emo = [c for c in cands if c.signal_type == "emotion_pattern"]
    assert len(emo) == 1
    assert len(emo[0].evidence) == 3


def test_rule_anomaly_fires_on_sharp_drop() -> None:
    """Prior window had 5 social events; current has 0 → anomaly candidate."""
    events = [
        # prior window (>14 days back from 2026-05-23, < 28d back)
        _ev("p1", "2026-05-01T10:00:00+08:00", domains=["social"]),
        _ev("p2", "2026-05-02T10:00:00+08:00", domains=["social"]),
        _ev("p3", "2026-05-03T10:00:00+08:00", domains=["social"]),
        _ev("p4", "2026-05-04T10:00:00+08:00", domains=["social"]),
        _ev("p5", "2026-05-05T10:00:00+08:00", domains=["social"]),
        # current window: nothing social, just unrelated activity
        _ev("c1", "2026-05-15T10:00:00+08:00", domains=["motor"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    anomaly = [c for c in cands if c.signal_type == "anomaly"]
    assert len(anomaly) == 1
    assert anomaly[0].domains == ["social"]
    # evidence is from the prior window (not current)
    assert all(e.id.startswith("p") for e in anomaly[0].evidence)
    assert len(anomaly[0].evidence) >= 2


def test_rule_anomaly_does_not_fire_when_prior_too_sparse() -> None:
    events = [
        _ev("p1", "2026-05-01T10:00:00+08:00", domains=["social"]),
        _ev("p2", "2026-05-02T10:00:00+08:00", domains=["social"]),
        # current window: nothing
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    assert [c for c in cands if c.signal_type == "anomaly"] == []


def test_rule_anomaly_does_not_fire_when_current_still_active() -> None:
    events = [
        _ev("p1", "2026-05-01T10:00:00+08:00", domains=["social"]),
        _ev("p2", "2026-05-02T10:00:00+08:00", domains=["social"]),
        _ev("p3", "2026-05-03T10:00:00+08:00", domains=["social"]),
        _ev("p4", "2026-05-04T10:00:00+08:00", domains=["social"]),
        # current still has 2 social events (not a drop)
        _ev("c1", "2026-05-15T10:00:00+08:00", domains=["social"]),
        _ev("c2", "2026-05-17T10:00:00+08:00", domains=["social"]),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    assert [c for c in cands if c.signal_type == "anomaly"] == []


def test_rule_emotion_pattern_same_day_does_not_count() -> None:
    """Three events but on two distinct days — under threshold."""
    events = [
        _ev("e1", "2026-05-15T08:00:00+08:00", emotions=["sad"], context="餐桌"),
        _ev("e2", "2026-05-15T18:00:00+08:00", emotions=["sad"], context="餐桌"),
        _ev("e3", "2026-05-17T08:00:00+08:00", emotions=["sad"], context="餐桌"),
    ]
    cands = propose_candidates(events, now_iso="2026-05-23T20:00:00+08:00")
    assert [c for c in cands if c.signal_type == "emotion_pattern"] == []


# ---- LLM layer (mocked) ---------------------------------------------------


class _StubLLM(LLMClient):
    """Returns canned JSON without HTTP — same trick as recorder unit tests."""

    def __init__(self, payloads: Iterator[str]) -> None:
        super().__init__()
        self._payloads = payloads

    def generate(self, prompt: str, **kwargs: object) -> LLMResult:
        return LLMResult(
            text=next(self._payloads),
            tokens_in=10,
            tokens_out=5,
            model_used="stub-model",
            backend="local",
            latency_ms=1,
        )


def _candidate() -> CandidateSignal:
    return CandidateSignal(
        signal_type="interest_pattern",
        domains=["music"],
        evidence=[
            _ev("e1", "2026-05-15T10:00:00+08:00", domains=["music"]),
            _ev("e2", "2026-05-19T10:00:00+08:00", domains=["music"]),
        ],
        rule_intensity_hint=0.5,
    )


def test_judge_accepts() -> None:
    payload = json.dumps(
        {"accept": True, "intensity": 0.7, "confidence": 0.85, "notes": "三次接触"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([payload])))
    v = se.judge(_candidate())
    assert v.accept is True
    assert v.intensity == 0.7
    assert v.confidence == 0.85
    assert v.notes == "三次接触"


def test_judge_rejects_with_zero_intensity() -> None:
    payload = json.dumps(
        {"accept": False, "intensity": 0.0, "confidence": 0.9, "notes": "证据不构成模式"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([payload])))
    v = se.judge(_candidate())
    assert v.accept is False


def test_judge_validates_intensity_range() -> None:
    payload = json.dumps(
        {"accept": True, "intensity": 1.7, "confidence": 0.5, "notes": "x"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([payload])))
    with pytest.raises(SignalExtractorError, match="intensity"):
        se.judge(_candidate())


def test_judge_rejects_non_bool_accept() -> None:
    payload = json.dumps(
        {"accept": "yes", "intensity": 0.5, "confidence": 0.5, "notes": "x"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([payload])))
    with pytest.raises(SignalExtractorError, match="accept"):
        se.judge(_candidate())


# ---- end-to-end (tmp DB + mocked LLM) ------------------------------------


def _insert_event(
    conn: sqlite3.Connection,
    *,
    eid: str,
    child_id: str,
    ts: str,
    type_: str = "observation",
    domains: list[str] | None = None,
    emotions: list[str] | None = None,
    context: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO events
          (id, child_id, timestamp, raw_text, summary, type,
           domains_json, emotions_json, context, source, model_used)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            child_id,
            ts,
            "原文",
            "摘要",
            type_,
            json.dumps(domains or ["music"], ensure_ascii=False),
            json.dumps(emotions or [], ensure_ascii=False),
            context,
            "manual",
            "qwen2.5:3b-instruct",
        ),
    )


def test_extract_for_child_persists_accepted_signal(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        for i, ts in enumerate(
            [
                "2026-05-15T10:00:00+08:00",
                "2026-05-17T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"e{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
    finally:
        conn.close()

    accepted_payload = json.dumps(
        {"accept": True, "intensity": 0.7, "confidence": 0.85, "notes": "音乐兴趣明显"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([accepted_payload])))
    signals = se.extract_for_child(
        child_id="xiaoming", now_iso="2026-05-23T20:00:00+08:00"
    )
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == "interest_pattern"
    assert sig.domains == ["music"]
    assert sig.intensity == 0.7
    # child_age_months: born 2023-06-01, last_seen 2026-05-19 → 35 months
    assert sig.child_age_months == 35
    assert isinstance(sig, Signal)

    # And the row survived the round trip:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        row = conn.execute(
            "SELECT * FROM signals WHERE child_id = ?", ("xiaoming",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["signal_type"] == "interest_pattern"


def test_extract_for_child_skips_rejected(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        for i, ts in enumerate(
            [
                "2026-05-15T10:00:00+08:00",
                "2026-05-17T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"e{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
    finally:
        conn.close()

    rejected = json.dumps(
        {"accept": False, "intensity": 0.0, "confidence": 0.9, "notes": "误报"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([rejected])))
    signals = se.extract_for_child(
        child_id="xiaoming", now_iso="2026-05-23T20:00:00+08:00"
    )
    assert signals == []


def test_extract_for_child_no_candidates_returns_empty(
    seeded_xiaoming: Path,
) -> None:
    """No events at all → no candidates → no LLM call → empty list."""
    se = SignalExtractor(llm=_StubLLM(iter([])))
    signals = se.extract_for_child(
        child_id="xiaoming", now_iso="2026-05-23T20:00:00+08:00"
    )
    assert signals == []


def test_extract_for_unknown_child_raises(seeded_xiaoming: Path) -> None:
    se = SignalExtractor(llm=_StubLLM(iter([])))
    with pytest.raises(SignalExtractorError, match="not found"):
        se.extract_for_child(child_id="ghost")


# ---- delta wiring (M1.3 follow-up) -----------------------------------------


def test_extract_writes_delta_when_prior_window_dense(seeded_xiaoming: Path) -> None:
    """Prior window has ≥ PRIOR_SPARSE_THRESHOLD events in the same domain →
    delta_from_last_period must be a number (not None) on the persisted Signal.

    Layout (window_days=14, now=2026-05-23):
      prior   [2026-04-25, 2026-05-09):  4 music events  → "active baseline"
      current [2026-05-09, 2026-05-23):  3 music events  → fires interest_pattern

    With prior=4 and current=3 events (both boosted equally since no
    prior signals), the delta is (3-4)/max = -0.25.
    """
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        # prior window — 4 events
        for i, ts in enumerate(
            [
                "2026-04-26T10:00:00+08:00",
                "2026-04-29T10:00:00+08:00",
                "2026-05-02T10:00:00+08:00",
                "2026-05-06T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"p{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
        # current window — 3 events (rule needs ≥3 to fire)
        for i, ts in enumerate(
            [
                "2026-05-15T10:00:00+08:00",
                "2026-05-17T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"c{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
    finally:
        conn.close()

    accepted_payload = json.dumps(
        {"accept": True, "intensity": 0.7, "confidence": 0.85, "notes": "ok"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([accepted_payload])))
    signals = se.extract_for_child(
        child_id="xiaoming",
        window_days=14,
        now_iso="2026-05-23T20:00:00+08:00",
    )
    assert len(signals) == 1
    sig = signals[0]
    assert sig.delta_from_last_period is not None
    # 3 vs 4 events, no signal-boosts because prior signals don't exist:
    # weighted = 3 vs 4 → (3-4)/4 = -0.25
    assert sig.delta_from_last_period == pytest.approx(-0.25)


def test_extract_leaves_delta_none_when_prior_sparse(
    seeded_xiaoming: Path,
) -> None:
    """Fewer than PRIOR_SPARSE_THRESHOLD prior events → delta stays None.

    PRD: "no data" must NOT pretend to be "no change".
    """
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        # prior: just 1 event (below threshold of 3)
        _insert_event(
            conn,
            eid="p1",
            child_id="xiaoming",
            ts="2026-05-01T10:00:00+08:00",
            domains=["music"],
        )
        # current: 3 events (so the rule fires)
        for i, ts in enumerate(
            [
                "2026-05-15T10:00:00+08:00",
                "2026-05-17T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"c{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
    finally:
        conn.close()

    accepted_payload = json.dumps(
        {"accept": True, "intensity": 0.7, "confidence": 0.85, "notes": "ok"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([accepted_payload])))
    signals = se.extract_for_child(
        child_id="xiaoming",
        window_days=14,
        now_iso="2026-05-23T20:00:00+08:00",
    )
    assert len(signals) == 1
    assert signals[0].delta_from_last_period is None


def test_extract_skips_delta_for_emotion_pattern(seeded_xiaoming: Path) -> None:
    """emotion_pattern signals carry aggregated event-domains, so the
    domain-counting delta would be misleading. Our policy: leave delta=None
    and let Phase 2 design a dedicated emotional-trend metric."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        # 3 distinct days same emotion same context (R3 fires)
        for i, ts in enumerate(
            [
                "2026-05-14T19:00:00+08:00",
                "2026-05-16T19:00:00+08:00",
                "2026-05-18T19:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn,
                eid=f"e{i}",
                child_id="xiaoming",
                ts=ts,
                domains=["routine"],
                emotions=["frustrated"],
                context="bedtime",
            )
        # also stuff prior window so signal_delta WOULD have computed
        # something if we hadn't short-circuited emotion_pattern
        for i, ts in enumerate(
            [
                "2026-04-26T19:00:00+08:00",
                "2026-04-28T19:00:00+08:00",
                "2026-05-01T19:00:00+08:00",
                "2026-05-04T19:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn,
                eid=f"p{i}",
                child_id="xiaoming",
                ts=ts,
                domains=["routine"],
                emotions=["frustrated"],
                context="bedtime",
            )
    finally:
        conn.close()

    accepted_payload = json.dumps(
        {"accept": True, "intensity": 0.6, "confidence": 0.8, "notes": "ok"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([accepted_payload])))
    signals = se.extract_for_child(
        child_id="xiaoming",
        window_days=14,
        now_iso="2026-05-23T20:00:00+08:00",
    )
    assert any(s.signal_type == "emotion_pattern" for s in signals)
    for s in signals:
        if s.signal_type == "emotion_pattern":
            assert s.delta_from_last_period is None


def test_log_disagreement_records_reject(
    seeded_xiaoming: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When the LLM rejects, an INFO log line with `signal.disagreement` is
    emitted carrying the rule_hint and llm confidence (Phase 2 prep)."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        for i, ts in enumerate(
            [
                "2026-05-15T10:00:00+08:00",
                "2026-05-17T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"e{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
    finally:
        conn.close()

    rejected = json.dumps(
        {"accept": False, "intensity": 0.0, "confidence": 0.9, "notes": "误报"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([rejected])))
    import logging
    with caplog.at_level(logging.INFO, logger="src.agents.signal_extractor"):
        signals = se.extract_for_child(
            child_id="xiaoming", now_iso="2026-05-23T20:00:00+08:00"
        )
    assert signals == []
    assert any("signal.disagreement reject" in r.message for r in caplog.records)


def test_log_disagreement_records_intensity_drift(
    seeded_xiaoming: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Rule layer hinted ~0.5 (3/6), LLM said 0.95 — drift ≥ 0.3 → log."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        for i, ts in enumerate(
            [
                "2026-05-15T10:00:00+08:00",
                "2026-05-17T10:00:00+08:00",
                "2026-05-19T10:00:00+08:00",
            ],
            start=1,
        ):
            _insert_event(
                conn, eid=f"e{i}", child_id="xiaoming", ts=ts, domains=["music"]
            )
    finally:
        conn.close()

    accepted_high = json.dumps(
        {"accept": True, "intensity": 0.95, "confidence": 0.9, "notes": "very strong"}
    )
    se = SignalExtractor(llm=_StubLLM(iter([accepted_high])))
    import logging
    with caplog.at_level(logging.INFO, logger="src.agents.signal_extractor"):
        signals = se.extract_for_child(
            child_id="xiaoming", now_iso="2026-05-23T20:00:00+08:00"
        )
    assert len(signals) == 1
    assert any(
        "signal.disagreement intensity_drift" in r.message for r in caplog.records
    )
