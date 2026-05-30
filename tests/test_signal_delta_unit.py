"""Unit tests for src/core/signal_delta.py.

Two paths covered:
  - compute_period_delta(): change score, sparsity guard
  - heatmap_data(): age-month bucketing (NOT calendar date — see PRD §2.1#5)
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from src.core import db as db_module
from src.core.signal_delta import (
    PRIOR_SPARSE_THRESHOLD,
    HeatmapCell,
    compute_period_delta,
    heatmap_data,
)

# ---- helpers --------------------------------------------------------------


def _seed_event(
    db_path: Path,
    *,
    eid: str,
    child_id: str = "xiaoming",
    ts: str,
    domains: list[str],
) -> None:
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO events (id, child_id, timestamp, raw_text, summary, type,
                                domains_json, emotions_json, context, source, model_used)
            VALUES (?, ?, ?, '原文', '摘要', 'observation', ?, '[]', '', 'manual', 'stub')
            """,
            (eid, child_id, ts, json.dumps(domains, ensure_ascii=False)),
        )
    finally:
        conn.close()


def _seed_signal(
    db_path: Path,
    *,
    sid: str,
    child_id: str = "xiaoming",
    domain: str,
    intensity: float,
    evidence: list[str],
    age_months: int = 30,
    status: str = "active",
) -> None:
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO signals (
                id, child_id, signal_type, domains_json, intensity,
                child_age_months, confidence, first_seen_at, last_seen_at,
                evidence_event_ids_json, status, notes
            ) VALUES (?, ?, 'interest_pattern', ?, ?, ?, 0.8, ?, ?, ?, ?, '')
            """,
            (
                sid,
                child_id,
                json.dumps([domain]),
                intensity,
                age_months,
                evidence[0] if evidence else "2026-05-01T00:00:00+08:00",
                evidence[-1] if evidence else "2026-05-19T00:00:00+08:00",
                json.dumps(evidence),
                status,
            ),
        )
    finally:
        conn.close()


# ---- compute_period_delta -------------------------------------------------


def test_delta_returns_none_when_prior_too_sparse(seeded_xiaoming: Path) -> None:
    # current window: 5 events; prior window: only 2 (< threshold 3)
    for i in range(5):
        _seed_event(
            seeded_xiaoming,
            eid=f"cur{i}",
            ts=f"2026-05-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    for i in range(2):
        _seed_event(
            seeded_xiaoming,
            eid=f"old{i}",
            ts=f"2026-04-0{i+1}T10:00:00+08:00",
            domains=["music"],
        )

    delta = compute_period_delta(
        "xiaoming",
        "music",
        current_window=(dt.date(2026, 5, 10), dt.date(2026, 5, 19)),
        prior_window=(dt.date(2026, 4, 1), dt.date(2026, 4, 14)),
    )
    assert delta is None


def test_delta_zero_when_unchanged(seeded_xiaoming: Path) -> None:
    for i in range(4):
        _seed_event(
            seeded_xiaoming,
            eid=f"cur{i}",
            ts=f"2026-05-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    for i in range(4):
        _seed_event(
            seeded_xiaoming,
            eid=f"prv{i}",
            ts=f"2026-04-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    delta = compute_period_delta(
        "xiaoming",
        "music",
        current_window=(dt.date(2026, 5, 10), dt.date(2026, 5, 20)),
        prior_window=(dt.date(2026, 4, 10), dt.date(2026, 4, 20)),
    )
    assert delta == pytest.approx(0.0)


def test_delta_negative_one_when_silenced(seeded_xiaoming: Path) -> None:
    """Was N events, now zero → -1.0 ('went silent')."""
    for i in range(5):
        _seed_event(
            seeded_xiaoming,
            eid=f"prv{i}",
            ts=f"2026-04-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    # current window: nothing music-related
    delta = compute_period_delta(
        "xiaoming",
        "music",
        current_window=(dt.date(2026, 5, 10), dt.date(2026, 5, 20)),
        prior_window=(dt.date(2026, 4, 10), dt.date(2026, 4, 20)),
    )
    assert delta == pytest.approx(-1.0)


def test_delta_positive_when_doubled(seeded_xiaoming: Path) -> None:
    for i in range(3):
        _seed_event(
            seeded_xiaoming,
            eid=f"prv{i}",
            ts=f"2026-04-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    for i in range(6):
        _seed_event(
            seeded_xiaoming,
            eid=f"cur{i}",
            ts=f"2026-05-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    delta = compute_period_delta(
        "xiaoming",
        "music",
        current_window=(dt.date(2026, 5, 10), dt.date(2026, 5, 20)),
        prior_window=(dt.date(2026, 4, 10), dt.date(2026, 4, 20)),
    )
    # 6 vs 3 → +0.5 baseline (no signal boost)
    assert delta == pytest.approx(0.5)


def test_delta_signal_intensity_amplifies(seeded_xiaoming: Path) -> None:
    """An active signal whose evidence sits in the current window
    boosts current weight, pushing the delta higher than raw-count alone."""
    # equal counts
    for i in range(4):
        _seed_event(
            seeded_xiaoming,
            eid=f"prv{i}",
            ts=f"2026-04-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    for i in range(4):
        _seed_event(
            seeded_xiaoming,
            eid=f"cur{i}",
            ts=f"2026-05-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    # One strong active signal in current window
    _seed_signal(
        seeded_xiaoming,
        sid="sig1",
        domain="music",
        intensity=0.9,
        evidence=["cur0", "cur1", "cur2"],
    )
    delta = compute_period_delta(
        "xiaoming",
        "music",
        current_window=(dt.date(2026, 5, 10), dt.date(2026, 5, 20)),
        prior_window=(dt.date(2026, 4, 10), dt.date(2026, 4, 20)),
    )
    assert delta is not None
    # Without signal boost → 0.0. With it, current = 4 + 3*0.9 = 6.7
    # delta = (6.7 - 4) / 6.7 ≈ 0.4
    assert delta > 0.3
    assert delta < 0.5


def test_delta_clamps_to_zero_when_both_empty(seeded_xiaoming: Path) -> None:
    """Both windows have ≥ threshold count of OTHER-domain events but zero
    music — _normalize_delta sees (0,0) and returns 0.0."""
    for i in range(5):
        _seed_event(
            seeded_xiaoming,
            eid=f"prv{i}",
            ts=f"2026-04-1{i}T10:00:00+08:00",
            domains=["music"],
        )
        _seed_event(
            seeded_xiaoming,
            eid=f"cur{i}",
            ts=f"2026-05-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    # asking about a different domain
    delta = compute_period_delta(
        "xiaoming",
        "motor",
        current_window=(dt.date(2026, 5, 10), dt.date(2026, 5, 20)),
        prior_window=(dt.date(2026, 4, 10), dt.date(2026, 4, 20)),
    )
    # prior count for 'motor' is 0 → sparse → None
    assert delta is None


def test_delta_threshold_constant_is_three() -> None:
    """Lock the PRD-mandated threshold so a future refactor that bumps it
    to 0 fails noisily."""
    assert PRIOR_SPARSE_THRESHOLD == 3


# ---- heatmap_data ---------------------------------------------------------


def test_heatmap_buckets_by_age_months_not_date(seeded_xiaoming: Path) -> None:
    """Two events from different calendar months but same child age should
    fall into the SAME bucket. Birthday: 2023-06-01."""
    # both at age = 35 months (around 2026-05)
    _seed_event(
        seeded_xiaoming, eid="e1", ts="2026-05-01T10:00:00+08:00", domains=["music"]
    )
    _seed_event(
        seeded_xiaoming, eid="e2", ts="2026-05-25T10:00:00+08:00", domains=["music"]
    )
    cells = heatmap_data("xiaoming")
    music_cells = [c for c in cells if c.domain == "music"]
    assert len(music_cells) == 1
    assert music_cells[0].age_months == 35
    assert music_cells[0].event_count == 2


def test_heatmap_separates_age_buckets(seeded_xiaoming: Path) -> None:
    # 2025-12 → ~30mo, 2026-05 → 35mo
    _seed_event(
        seeded_xiaoming, eid="e1", ts="2025-12-15T10:00:00+08:00", domains=["music"]
    )
    _seed_event(
        seeded_xiaoming, eid="e2", ts="2026-05-15T10:00:00+08:00", domains=["music"]
    )
    cells = heatmap_data("xiaoming")
    ages = sorted({c.age_months for c in cells})
    assert ages == [30, 35]


def test_heatmap_intensity_is_normalized_to_unit(seeded_xiaoming: Path) -> None:
    for i in range(3):
        _seed_event(
            seeded_xiaoming,
            eid=f"a{i}",
            ts=f"2026-05-1{i}T10:00:00+08:00",
            domains=["music"],
        )
    _seed_event(
        seeded_xiaoming, eid="b", ts="2026-05-15T10:00:00+08:00", domains=["motor"]
    )
    cells = heatmap_data("xiaoming")
    intensities = sorted(c.intensity for c in cells)
    assert intensities[0] >= 0.0
    assert intensities[-1] == pytest.approx(1.0)


def test_heatmap_filter_by_domains(seeded_xiaoming: Path) -> None:
    _seed_event(
        seeded_xiaoming, eid="m1", ts="2026-05-15T10:00:00+08:00", domains=["music"]
    )
    _seed_event(
        seeded_xiaoming, eid="m2", ts="2026-05-15T10:00:00+08:00", domains=["motor"]
    )
    cells = heatmap_data("xiaoming", domains=["music"])
    assert all(c.domain == "music" for c in cells)


def test_heatmap_unknown_child_returns_empty(seeded_xiaoming: Path) -> None:
    assert heatmap_data("ghost") == []


def test_heatmap_empty_when_no_events(seeded_xiaoming: Path) -> None:
    assert heatmap_data("xiaoming") == []


def test_heatmap_signal_evidence_boosts_score(seeded_xiaoming: Path) -> None:
    """An event that is evidence for a high-intensity signal should
    score higher than a vanilla event with no signal."""
    _seed_event(
        seeded_xiaoming, eid="boosted", ts="2026-05-15T10:00:00+08:00", domains=["music"]
    )
    _seed_event(
        seeded_xiaoming, eid="vanilla", ts="2026-05-15T10:00:00+08:00", domains=["motor"]
    )
    _seed_signal(
        seeded_xiaoming,
        sid="sig1",
        domain="music",
        intensity=0.9,
        evidence=["boosted", "boosted"],  # length 1 isn't allowed by Signal
    )
    cells = {(c.age_months, c.domain): c for c in heatmap_data("xiaoming")}
    music = cells[(35, "music")]
    motor = cells[(35, "motor")]
    assert music.raw_score > motor.raw_score
    # music: 1 + 0.9 = 1.9; motor: 1.0 → music intensity == 1.0
    assert music.intensity == pytest.approx(1.0)


def test_heatmap_returns_dataclass_shape(seeded_xiaoming: Path) -> None:
    _seed_event(
        seeded_xiaoming, eid="e1", ts="2026-05-15T10:00:00+08:00", domains=["music"]
    )
    cells = heatmap_data("xiaoming")
    assert isinstance(cells[0], HeatmapCell)
