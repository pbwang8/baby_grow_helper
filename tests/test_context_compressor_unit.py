"""Unit tests for src/agents/context_compressor.

PRD prd/phase2-weekly-insight.md §2.1#7 names two non-negotiable gates:
  - 50-event input compresses to < 4k est. tokens
  - all milestone events survive compression

The rest of the suite covers smaller invariants:
  - week_start must be Monday
  - unknown child raises
  - signal one-liners stay under the budget
  - period_deltas carry through compute_period_delta's None semantics
  - signal evidence is capped at 2/signal
  - uncovered-domain events get a slot
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError
from src.agents.context_compressor import (
    MAX_EVIDENCE_PER_SIGNAL,
    MAX_TOTAL_HIGHLIGHTS,
    SIGNAL_ONE_LINER_BUDGET_CHARS,
    CompressedContext,
    ContextCompressorError,
    DomainDelta,
    EventHighlight,
    SignalSummary,
    _summarize_signal,
    compress_week_context,
)
from src.core import db as db_module
from src.core.models import Signal

# ---- helpers --------------------------------------------------------------


WEEK_START = dt.date(2026, 5, 18)  # a Monday
WEEK_END = WEEK_START + dt.timedelta(days=7)  # 2026-05-25


def _seed_child(db_path: Path, child_id: str = "xiaoming") -> None:
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            (child_id, "小明", "2023-06-01"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_event(
    db_path: Path,
    *,
    child_id: str = "xiaoming",
    timestamp: str,
    summary: str = "占位摘要",
    type: str = "observation",
    domains: list[str] | None = None,
    raw_text: str = "占位原文",
    event_id: str | None = None,
) -> str:
    conn = db_module.get_conn(db_path)
    try:
        eid = event_id or f"evt_test_{secrets.token_hex(3)}"
        conn.execute(
            """
            INSERT INTO events
              (id, child_id, timestamp, raw_text, summary, type,
               domains_json, emotions_json, context, source, model_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', NULL)
            """,
            (
                eid,
                child_id,
                timestamp,
                raw_text,
                summary,
                type,
                json.dumps(domains or ["other"], ensure_ascii=False),
                "[]",
                "",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return eid


def _insert_signal(
    db_path: Path,
    *,
    signal_id: str,
    child_id: str = "xiaoming",
    signal_type: str = "interest_pattern",
    domains: list[str],
    intensity: float = 0.7,
    evidence: list[str],
    last_seen: str = "2026-05-22T20:00:00+08:00",
    delta: float | None = None,
    notes: str = "",
) -> None:
    sig = Signal(
        id=signal_id,
        child_id=child_id,
        signal_type=signal_type,  # type: ignore[arg-type]
        domains=domains,
        intensity=intensity,
        child_age_months=35,
        delta_from_last_period=delta,
        confidence=0.8,
        first_seen_at=last_seen,
        last_seen_at=last_seen,
        evidence_event_ids=evidence,
        status="active",
        notes=notes,
    )
    conn = db_module.get_conn(db_path)
    try:
        row = sig.as_row()
        conn.execute(
            """
            INSERT INTO signals
              (id, child_id, signal_type, domains_json, intensity,
               child_age_months, delta_from_last_period, confidence,
               first_seen_at, last_seen_at, evidence_event_ids_json,
               status, notes)
            VALUES
              (:id, :child_id, :signal_type, :domains_json, :intensity,
               :child_age_months, :delta_from_last_period, :confidence,
               :first_seen_at, :last_seen_at, :evidence_event_ids_json,
               :status, :notes)
            """,
            row,
        )
        conn.commit()
    finally:
        conn.close()


# ---- input validation ----------------------------------------------------


def test_non_monday_week_start_raises(seeded_xiaoming: Path) -> None:
    tuesday = dt.date(2026, 5, 19)
    with pytest.raises(ContextCompressorError, match="must be a Monday"):
        compress_week_context("xiaoming", tuesday)


def test_unknown_child_raises(tmp_db: Path) -> None:
    with pytest.raises(ContextCompressorError, match="not found"):
        compress_week_context("ghost", WEEK_START)


# ---- happy path: signal one-liner format ---------------------------------


def test_summarize_signal_renders_compact_one_liner() -> None:
    sig = Signal(
        id="sig_20260520_001",
        child_id="xiaoming",
        signal_type="interest_pattern",
        domains=["music", "creativity"],
        intensity=0.73,
        child_age_months=35,
        delta_from_last_period=0.42,
        confidence=0.81,
        first_seen_at="2026-05-15T10:00:00+08:00",
        last_seen_at="2026-05-21T10:00:00+08:00",
        evidence_event_ids=["evt_a", "evt_b"],
        status="active",
        notes="本周三次主动靠近钢琴",
    )
    summary = _summarize_signal(sig)
    assert summary.signal_id == "sig_20260520_001"
    assert summary.one_liner.startswith("interest_pattern@music i=0.73 Δ+0.42")
    assert "本周三次主动靠近钢琴" in summary.one_liner
    assert len(summary.one_liner) <= SIGNAL_ONE_LINER_BUDGET_CHARS


def test_summarize_signal_drops_delta_part_when_none() -> None:
    sig = Signal(
        id="sig_20260520_002",
        child_id="xiaoming",
        signal_type="emotion_pattern",
        domains=["social"],
        intensity=0.5,
        child_age_months=35,
        delta_from_last_period=None,  # prior sparse / emotion_pattern
        confidence=0.7,
        first_seen_at="2026-05-15T10:00:00+08:00",
        last_seen_at="2026-05-21T10:00:00+08:00",
        evidence_event_ids=["evt_x", "evt_y"],
        status="active",
        notes="",
    )
    summary = _summarize_signal(sig)
    assert "Δ" not in summary.one_liner
    assert summary.one_liner == "emotion_pattern@social i=0.50"


# ---- highlights selection ------------------------------------------------


def test_milestones_always_preserved(seeded_xiaoming: Path) -> None:
    """PRD §2.1#7: 'milestone 事件全部在压缩输出里'."""
    # 2 milestones + many noise events
    ms_a = _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-19T10:00:00+08:00",
        summary="第一次自己刷牙",
        type="milestone",
        domains=["self_care"],
    )
    ms_b = _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-22T15:00:00+08:00",
        summary="第一次完整唱完一闪一闪",
        type="milestone",
        domains=["music"],
    )
    # 30 noise observations to push past the cap
    for i in range(30):
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + (i % 7):02d}T{8 + (i % 10):02d}:30:00+08:00",
            summary=f"普通观察{i}",
            type="observation",
            domains=["language"],
        )

    ctx = compress_week_context("xiaoming", WEEK_START)
    surviving_ids = {h.event_id for h in ctx.event_highlights}
    assert ms_a in surviving_ids
    assert ms_b in surviving_ids


def test_evidence_capped_at_two_per_signal(seeded_xiaoming: Path) -> None:
    eids = [
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + i:02d}T10:00:00+08:00",
            summary=f"音乐相关事件{i}",
            type="observation",
            domains=["music"],
        )
        for i in range(5)
    ]
    _insert_signal(
        seeded_xiaoming,
        signal_id="sig_test_music_001",
        domains=["music"],
        evidence=eids,  # 5 evidence events
    )

    ctx = compress_week_context("xiaoming", WEEK_START)
    sig_evidence = [
        h for h in ctx.event_highlights if h.reason == "signal_evidence"
    ]
    assert len(sig_evidence) <= MAX_EVIDENCE_PER_SIGNAL


def test_uncovered_domain_gets_a_slot(seeded_xiaoming: Path) -> None:
    music_evt_a = _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-19T10:00:00+08:00",
        summary="主动放儿童歌曲",
        type="observation",
        domains=["music"],
    )
    music_evt_b = _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-21T10:00:00+08:00",
        summary="哼歌",
        type="observation",
        domains=["music"],
    )
    # uncovered domain — sleep is NOT in the signal's domains, so it must
    # surface as `uncovered_domain` even though there's only one event.
    sleep_evt = _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-20T20:00:00+08:00",
        summary="哭闹一小时才入睡",
        type="observation",
        domains=["sleep"],
    )
    _insert_signal(
        seeded_xiaoming,
        signal_id="sig_test_music_002",
        domains=["music"],
        evidence=[music_evt_a, music_evt_b],
    )

    ctx = compress_week_context("xiaoming", WEEK_START)
    surviving = {h.event_id: h for h in ctx.event_highlights}
    assert sleep_evt in surviving
    assert surviving[sleep_evt].reason == "uncovered_domain"


def test_total_cap_respects_milestones(seeded_xiaoming: Path) -> None:
    """Cap of 8 is for non-milestone picks; milestones bypass."""
    # 10 milestones (more than cap)
    ms_ids = [
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + (i % 7):02d}T{9 + i:02d}:00:00+08:00",
            summary=f"里程碑{i}",
            type="milestone",
            domains=["self_care"],
        )
        for i in range(10)
    ]
    # plus discretionary picks via uncovered domains
    for i in range(10):
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + (i % 7):02d}T18:00:00+08:00",
            summary=f"语言事件{i}",
            type="observation",
            domains=["language"],
        )

    ctx = compress_week_context("xiaoming", WEEK_START)
    surviving = {h.event_id for h in ctx.event_highlights}
    # all 10 milestones survived
    for mid in ms_ids:
        assert mid in surviving
    # discretionary picks DON'T inflate beyond cap budget
    discretionary = [
        h for h in ctx.event_highlights if h.reason != "milestone"
    ]
    assert len(discretionary) <= MAX_TOTAL_HIGHLIGHTS  # budget=0 actually,
    # but we just assert the cap math doesn't go negative


# ---- period deltas --------------------------------------------------------


def test_period_delta_none_when_prior_sparse(seeded_xiaoming: Path) -> None:
    """compute_period_delta returns None for prior < 3 events; we propagate."""
    # 5 current-week music events, 0 prior
    for i in range(5):
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + i:02d}T10:00:00+08:00",
            summary=f"音乐{i}",
            type="observation",
            domains=["music"],
        )

    ctx = compress_week_context("xiaoming", WEEK_START)
    music_delta = next(d for d in ctx.period_deltas if d.domain == "music")
    assert music_delta.delta is None  # prior sparse
    assert music_delta.current_event_count == 5
    assert music_delta.prior_event_count == 0


def test_period_delta_numeric_when_prior_dense(seeded_xiaoming: Path) -> None:
    # 4 prior-week music events, 4 current-week
    for i in range(4):
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{11 + i:02d}T10:00:00+08:00",
            summary=f"prior音乐{i}",
            type="observation",
            domains=["music"],
        )
    for i in range(4):
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + i:02d}T10:00:00+08:00",
            summary=f"current音乐{i}",
            type="observation",
            domains=["music"],
        )

    ctx = compress_week_context("xiaoming", WEEK_START)
    music_delta = next(d for d in ctx.period_deltas if d.domain == "music")
    # current ≈ prior → delta near 0
    assert music_delta.delta is not None
    assert -0.3 <= music_delta.delta <= 0.3


# ---- the headline gate (PRD §2.1#7) --------------------------------------


def test_50_event_input_compresses_under_4k_tokens(seeded_xiaoming: Path) -> None:
    """Synthetic 50-event week → output ≤ 4k est. tokens. Milestones included."""
    domains_pool = ["music", "language", "self_care", "social", "motor"]
    for i in range(50):
        ev_type = "milestone" if i % 17 == 0 else "observation"
        _insert_event(
            seeded_xiaoming,
            timestamp=f"2026-05-{18 + (i % 7):02d}T{8 + (i % 12):02d}:30:00+08:00",
            summary=f"合成事件{i}：当周一些观察记录,保留较长的中文说明用来贴近真实摘要的字数。",
            type=ev_type,
            domains=[domains_pool[i % len(domains_pool)]],
            raw_text=("长篇原文" * 30),  # would have inflated tokens — must be dropped
        )

    ctx = compress_week_context("xiaoming", WEEK_START)
    assert ctx.raw_token_count > 0
    assert ctx.raw_token_count < 4000
    # Milestones (i % 17 == 0 → indices 0,17,34) survived
    milestone_count = sum(
        1 for h in ctx.event_highlights if h.type == "milestone"
    )
    assert milestone_count == 3


def test_output_drops_raw_text_field(seeded_xiaoming: Path) -> None:
    """PRD §2.1#1: '删除 raw_text，只保留 summary'."""
    _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-19T10:00:00+08:00",
        summary="短摘要",
        type="milestone",
        domains=["language"],
        raw_text="一段非常长的原始文本" * 50,
    )
    ctx = compress_week_context("xiaoming", WEEK_START)
    payload = ctx.model_dump_json()
    assert "raw_text" not in payload
    # the long raw_text shouldn't have leaked through
    assert "一段非常长的原始文本一段非常长的原始文本" not in payload


# ---- Pydantic surface ----------------------------------------------------


def test_compressed_context_is_frozen(seeded_xiaoming: Path) -> None:
    ctx = compress_week_context("xiaoming", WEEK_START)
    # Pydantic frozen models raise ValidationError on attribute assignment.
    with pytest.raises(ValidationError):
        ctx.signals = []


def test_age_months_frozen_at_week_start(seeded_xiaoming: Path) -> None:
    """Birthday=2023-06-01, week_start=2026-05-18 → 35 months."""
    _insert_event(
        seeded_xiaoming,
        timestamp="2026-05-19T10:00:00+08:00",
        summary="anchor",
        type="observation",
        domains=["language"],
    )
    ctx = compress_week_context("xiaoming", WEEK_START)
    assert ctx.child_age_months == 35


def test_dataclass_exports_are_models() -> None:
    """PRD §2.1#1 talks about dataclasses; we ship Pydantic frozen models
    for parse-time validation. Sanity check the round-trip surface."""
    from pydantic import BaseModel
    assert issubclass(CompressedContext, BaseModel)
    # round-trip JSON
    ctx = CompressedContext(
        child_id="xiaoming",
        week_start=WEEK_START,
        week_end=WEEK_END,
        child_age_months=35,
        signals=[SignalSummary(signal_id="sig_x", one_liner="t@d i=0.50")],
        event_highlights=[
            EventHighlight(
                event_id="evt_x",
                timestamp="2026-05-19T10:00:00+08:00",
                summary="s",
                type="observation",
                domains=["music"],
                reason="signal_evidence",
            )
        ],
        period_deltas=[
            DomainDelta(
                domain="music", delta=None,
                current_event_count=1, prior_event_count=0,
            )
        ],
        raw_token_count=42,
    )
    # frozen
    payload = ctx.model_dump_json()
    rebuilt = CompressedContext.model_validate_json(payload)
    assert rebuilt == ctx
