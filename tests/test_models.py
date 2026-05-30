"""Tests for src/core/models.py — Signal Pydantic model + helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from src.core import db as db_module
from src.core.models import (
    ALLOWED_SIGNAL_STATUSES,
    ALLOWED_SIGNAL_TYPES,
    Signal,
    compute_age_months,
    new_signal_id,
)


def _good_signal_kwargs() -> dict[str, object]:
    return {
        "id": "sig_20260519_001",
        "child_id": "xiaoming",
        "signal_type": "interest_pattern",
        "domains": ["music"],
        "intensity": 0.7,
        "child_age_months": 35,
        "delta_from_last_period": 0.4,
        "confidence": 0.8,
        "first_seen_at": "2026-05-05T10:00:00+08:00",
        "last_seen_at": "2026-05-19T10:00:00+08:00",
        "evidence_event_ids": ["evt_a", "evt_b", "evt_c"],
    }


# ---- happy path -----------------------------------------------------------


def test_signal_happy_path_round_trip() -> None:
    sig = Signal(**_good_signal_kwargs())  # type: ignore[arg-type]
    row = sig.as_row()
    assert row["id"] == "sig_20260519_001"
    assert row["domains_json"] == '["music"]'
    assert row["evidence_event_ids_json"] == '["evt_a", "evt_b", "evt_c"]'

    back = Signal.from_row(row)
    assert back == sig


def test_signal_default_status_active() -> None:
    sig = Signal(**_good_signal_kwargs())  # type: ignore[arg-type]
    assert sig.status == "active"
    assert sig.notes == ""


# ---- validation rules per PRD --------------------------------------------


def test_signal_rejects_single_evidence() -> None:
    bad = _good_signal_kwargs() | {"evidence_event_ids": ["evt_only_one"]}
    with pytest.raises(ValidationError, match="evidence_event_ids"):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_rejects_duplicate_evidence() -> None:
    bad = _good_signal_kwargs() | {"evidence_event_ids": ["evt_a", "evt_a"]}
    with pytest.raises(ValidationError, match="unique"):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_rejects_intensity_out_of_range() -> None:
    bad = _good_signal_kwargs() | {"intensity": 1.5}
    with pytest.raises(ValidationError):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_rejects_confidence_negative() -> None:
    bad = _good_signal_kwargs() | {"confidence": -0.1}
    with pytest.raises(ValidationError):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_rejects_unknown_type() -> None:
    bad = _good_signal_kwargs() | {"signal_type": "talent_spotting"}
    with pytest.raises(ValidationError):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_rejects_unknown_status() -> None:
    bad = _good_signal_kwargs() | {"status": "archived"}
    with pytest.raises(ValidationError):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_delta_can_be_none() -> None:
    """Per PRD: 'no data' must not pretend to be 'no change'."""
    kwargs = _good_signal_kwargs() | {"delta_from_last_period": None}
    sig = Signal(**kwargs)  # type: ignore[arg-type]
    assert sig.delta_from_last_period is None
    row = sig.as_row()
    assert row["delta_from_last_period"] is None


def test_signal_delta_clamps_to_unit_interval() -> None:
    bad = _good_signal_kwargs() | {"delta_from_last_period": 1.2}
    with pytest.raises(ValidationError):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_domains_dedupe() -> None:
    kwargs = _good_signal_kwargs() | {"domains": ["music", "music", "creativity"]}
    sig = Signal(**kwargs)  # type: ignore[arg-type]
    assert sig.domains == ["music", "creativity"]


def test_signal_rejects_empty_domains() -> None:
    bad = _good_signal_kwargs() | {"domains": []}
    with pytest.raises(ValidationError):
        Signal(**bad)  # type: ignore[arg-type]


def test_signal_is_frozen() -> None:
    sig = Signal(**_good_signal_kwargs())  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        sig.intensity = 0.99


def test_signal_extra_field_forbidden() -> None:
    bad = _good_signal_kwargs() | {"unknown_field": "x"}
    with pytest.raises(ValidationError, match="extra"):
        Signal(**bad)  # type: ignore[arg-type]


# ---- helpers --------------------------------------------------------------


def test_compute_age_months_basic() -> None:
    # born 2023-06-01, "now" = 2026-05-23 → 35 months (May < June birthday day check)
    # 2023-06 → 2026-05 = 2y 11m = 35 months
    assert compute_age_months("2023-06-01", "2026-05-23T10:00:00+08:00") == 35


def test_compute_age_months_birthday_day_rollback() -> None:
    # born 2023-06-15, "now" = 2026-06-10 → 35 months (haven't hit June 15 yet)
    assert compute_age_months("2023-06-15", "2026-06-10T10:00:00+08:00") == 35
    # 2026-06-15 exactly → 36 months
    assert compute_age_months("2023-06-15", "2026-06-15T10:00:00+08:00") == 36


def test_compute_age_months_pre_birthday_clamps_to_zero() -> None:
    # 'now' before child's birth — degenerate but mustn't go negative
    assert compute_age_months("2023-06-01", "2023-05-01T00:00:00+08:00") == 0


def test_new_signal_id_format() -> None:
    sid = new_signal_id("2026-05-23T10:00:00+08:00", 1)
    assert sid == "sig_20260523_001"
    assert new_signal_id("2026-05-23T10:00:00+08:00", 42) == "sig_20260523_042"


# ---- DB roundtrip --------------------------------------------------------


def test_signal_writes_and_reads_through_db(seeded_xiaoming: Path) -> None:
    sig = Signal(**_good_signal_kwargs())  # type: ignore[arg-type]
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        with db_module.transactional(conn):
            cols = ", ".join(sig.as_row().keys())
            placeholders = ", ".join(f":{k}" for k in sig.as_row())
            conn.execute(
                f"INSERT INTO signals ({cols}) VALUES ({placeholders})",
                sig.as_row(),
            )
        row = conn.execute(
            "SELECT * FROM signals WHERE id = ?", (sig.id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    back = Signal.from_row(dict(row))
    assert back == sig


def test_closed_sets_self_consistent() -> None:
    """The Literal types must be in sync with the frozensets we re-export."""
    assert "interest_pattern" in ALLOWED_SIGNAL_TYPES
    assert "active" in ALLOWED_SIGNAL_STATUSES
    assert len(ALLOWED_SIGNAL_TYPES) == 5
    assert len(ALLOWED_SIGNAL_STATUSES) == 3
