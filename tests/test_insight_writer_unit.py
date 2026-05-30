"""Unit tests for src/agents/insight_writer.

PRD prd/phase2-weekly-insight.md hard constraints:
  - exactly 4 sections (§2.1#3)
  - at least one section.axis == 'change_over_time' (§10.1)
  - sources_used ⊆ input signal_ids ∪ event_ids (§3.7)
  - violation → retry once → degrade if still bad

These are the gates the suite watches. Backend-level wiring (cloud route,
prompt caching) is verified in test_llm_client.py — here we mock LLMClient
to keep the agent layer tests deterministic and offline.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from src.agents.context_compressor import (
    CompressedContext,
    DomainDelta,
    EventHighlight,
    SignalSummary,
)
from src.agents.insight_writer import (
    InsightWriter,
    InsightWriterError,
    WeeklyInsight,
    write_weekly_insight,
)
from src.core.llm_client import LLMResult

# ---- fixtures -------------------------------------------------------------


WEEK_START = dt.date(2026, 5, 18)
WEEK_END = WEEK_START + dt.timedelta(days=7)


def _ctx(
    *,
    signal_ids: list[str] | None = None,
    event_ids: list[str] | None = None,
) -> CompressedContext:
    sigs = [
        SignalSummary(signal_id=sid, one_liner=f"interest_pattern@music i=0.70 — {sid}")
        for sid in (signal_ids or ["sig_001"])
    ]
    events = [
        EventHighlight(
            event_id=eid,
            timestamp="2026-05-19T10:00:00+08:00",
            summary=f"事件 {eid}",
            type="observation",
            domains=["music"],
            reason="signal_evidence",
        )
        for eid in (event_ids or ["evt_a", "evt_b"])
    ]
    return CompressedContext(
        child_id="xiaoming",
        week_start=WEEK_START,
        week_end=WEEK_END,
        child_age_months=35,
        signals=sigs,
        event_highlights=events,
        period_deltas=[
            DomainDelta(
                domain="music", delta=0.4,
                current_event_count=3, prior_event_count=2,
            )
        ],
        raw_token_count=900,
    )


def _good_writer_response(sources: list[str]) -> dict[str, Any]:
    """A well-formed writer JSON output."""
    return {
        "sections": [
            {
                "axis": "highlight",
                "title": "对节奏的兴趣",
                "body": f"本周三次接触音乐 ({sources[0]})。",
                "sources_used": [sources[0]],
            },
            {
                "axis": "change_over_time",
                "title": "趋势在加深",
                "body": f"上周 2 次本周 3 次 ({sources[1]})。",
                "sources_used": [sources[1]],
            },
            {
                "axis": "next_week_focus",
                "title": "下周可观察",
                "body": "节奏感持续吗？",
                "sources_used": [],
            },
            {
                "axis": "open_questions",
                "title": "开放问题",
                "body": "她对哪种音乐最有反应？",
                "sources_used": [],
            },
        ],
        "open_questions": ["她对哪种音乐最有反应？"],
        "sources_used": sources,
    }


def _mock_llm(text: str, *, tokens_in: int = 1000, tokens_out: int = 200) -> MagicMock:
    """Build a MagicMock LLMClient that returns `text`."""
    llm = MagicMock()
    llm.generate.return_value = LLMResult(
        text=text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model_used="claude-sonnet-4-20250514",
        backend="cloud",
        latency_ms=120,
        cache_creation_tokens=0,
        cache_read_tokens=900,
    )
    return llm


# ---- happy path -----------------------------------------------------------


def test_write_weekly_insight_happy_path() -> None:
    ctx = _ctx(signal_ids=["sig_001"], event_ids=["evt_a", "evt_b"])
    payload = _good_writer_response(["sig_001", "evt_a"])
    llm = _mock_llm(json.dumps(payload, ensure_ascii=False))

    insight = write_weekly_insight(ctx, backend="claude", llm=llm)

    assert isinstance(insight, WeeklyInsight)
    assert insight.child_id == "xiaoming"
    assert insight.backend == "claude"
    assert len(insight.sections) == 4
    assert insight.tokens_in == 1000
    assert insight.tokens_out == 200
    # PRD §10.1: change_over_time present
    assert any(s.axis == "change_over_time" for s in insight.sections)


def test_writer_calls_cloud_with_cache_system_true() -> None:
    ctx = _ctx()
    payload = _good_writer_response(["sig_001", "evt_a"])
    llm = _mock_llm(json.dumps(payload, ensure_ascii=False))
    write_weekly_insight(ctx, backend="claude", llm=llm)

    call = llm.generate.call_args
    assert call.kwargs["backend"] == "cloud"
    assert call.kwargs["purpose"] == "insight"
    assert call.kwargs["cache_system"] is True
    # The system prompt must come from the file (not inlined) — we check it's
    # at least non-empty and contains the writer's signature line.
    assert "BabyGrowHelper · 周报 Agent" in call.kwargs["system"]


def test_local_fallback_routes_to_local_backend() -> None:
    ctx = _ctx()
    payload = _good_writer_response(["sig_001", "evt_a"])
    llm = _mock_llm(json.dumps(payload, ensure_ascii=False))
    write_weekly_insight(ctx, backend="local-fallback", llm=llm)
    assert llm.generate.call_args.kwargs["backend"] == "local"
    # local route uses json_mode (Ollama format=json)
    assert llm.generate.call_args.kwargs["json_mode"] is True


def test_writer_id_is_uuid4_hex() -> None:
    ctx = _ctx()
    llm = _mock_llm(json.dumps(_good_writer_response(["sig_001", "evt_a"])))
    insight = write_weekly_insight(ctx, backend="claude", llm=llm)
    assert len(insight.id) == 32
    assert all(c in "0123456789abcdef" for c in insight.id)


# ---- hard constraint: change_over_time required (PRD §10.1) ---------------


def test_writer_retries_when_change_over_time_missing() -> None:
    ctx = _ctx()
    bad = _good_writer_response(["sig_001", "evt_a"])
    # swap change_over_time → highlight to violate §10.1
    bad["sections"][1]["axis"] = "highlight"
    good = _good_writer_response(["sig_001", "evt_a"])

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text=json.dumps(bad), tokens_in=900, tokens_out=200,
                  model_used="claude-sonnet-4", backend="cloud",
                  latency_ms=100, cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(good), tokens_in=950, tokens_out=210,
                  model_used="claude-sonnet-4", backend="cloud",
                  latency_ms=110, cache_creation_tokens=0, cache_read_tokens=0),
    ]

    insight = write_weekly_insight(ctx, backend="claude", llm=llm)
    assert llm.generate.call_count == 2
    assert any(s.axis == "change_over_time" for s in insight.sections)


# ---- hard constraint: sources_used ⊆ input ids (PRD §3.7) -----------------


def test_writer_retries_when_sources_used_has_unknown_id() -> None:
    ctx = _ctx(signal_ids=["sig_001"], event_ids=["evt_a", "evt_b"])
    bad = _good_writer_response(["sig_001", "evt_GHOST"])
    good = _good_writer_response(["sig_001", "evt_a"])

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text=json.dumps(bad), tokens_in=1000, tokens_out=200,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(good), tokens_in=1000, tokens_out=200,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
    ]

    insight = write_weekly_insight(ctx, backend="claude", llm=llm)
    assert llm.generate.call_count == 2
    assert "evt_a" in insight.sources_used
    assert "evt_GHOST" not in insight.sources_used


def test_section_level_sources_used_also_validated() -> None:
    """Per-section sources_used must also subset input ids."""
    ctx = _ctx(signal_ids=["sig_001"], event_ids=["evt_a", "evt_b"])
    bad = _good_writer_response(["sig_001", "evt_a"])
    bad["sections"][0]["sources_used"] = ["evt_NOTREAL"]  # poison one section
    good = _good_writer_response(["sig_001", "evt_a"])

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text=json.dumps(bad), tokens_in=1000, tokens_out=200,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(good), tokens_in=1000, tokens_out=200,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
    ]
    write_weekly_insight(ctx, backend="claude", llm=llm)
    assert llm.generate.call_count == 2


# ---- hard constraint: degrade after second failure ------------------------


def test_writer_degrades_after_two_failures() -> None:
    """PRD §3.7 fallback path: two violations → benign placeholder, no raise."""
    ctx = _ctx()
    bad = _good_writer_response(["sig_001", "evt_GHOST"])  # bad sources

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text=json.dumps(bad), tokens_in=1000, tokens_out=200,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(bad), tokens_in=1000, tokens_out=200,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
    ]

    insight = write_weekly_insight(ctx, backend="claude", llm=llm)
    assert llm.generate.call_count == 2
    assert insight.model_used == "degraded"
    assert insight.sources_used == []
    # still satisfies schema (4 sections, ≥1 change_over_time)
    assert len(insight.sections) == 4
    assert any(s.axis == "change_over_time" for s in insight.sections)


# ---- input shape ----------------------------------------------------------


def test_writer_section_count_must_be_four() -> None:
    ctx = _ctx()
    bad = _good_writer_response(["sig_001", "evt_a"])
    bad["sections"] = bad["sections"][:3]  # only 3 sections
    good = _good_writer_response(["sig_001", "evt_a"])

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text=json.dumps(bad), tokens_in=1, tokens_out=1,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(good), tokens_in=1, tokens_out=1,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
    ]
    insight = write_weekly_insight(ctx, backend="claude", llm=llm)
    assert len(insight.sections) == 4


def test_writer_open_questions_must_be_in_range() -> None:
    ctx = _ctx()
    bad = _good_writer_response(["sig_001", "evt_a"])
    bad["open_questions"] = []  # below minimum
    good = _good_writer_response(["sig_001", "evt_a"])

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text=json.dumps(bad), tokens_in=1, tokens_out=1,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(good), tokens_in=1, tokens_out=1,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
    ]
    write_weekly_insight(ctx, backend="claude", llm=llm)
    assert llm.generate.call_count == 2


def test_writer_handles_non_json_response_via_retry() -> None:
    ctx = _ctx()
    good = _good_writer_response(["sig_001", "evt_a"])

    llm = MagicMock()
    llm.generate.side_effect = [
        LLMResult(text="this is not JSON at all", tokens_in=1, tokens_out=1,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
        LLMResult(text=json.dumps(good), tokens_in=1, tokens_out=1,
                  model_used="m", backend="cloud", latency_ms=10,
                  cache_creation_tokens=0, cache_read_tokens=0),
    ]
    insight = write_weekly_insight(ctx, backend="claude", llm=llm)
    assert llm.generate.call_count == 2
    assert insight.model_used != "degraded"


# ---- prompt rendering -----------------------------------------------------


def test_user_prompt_contains_signal_ids_and_event_ids() -> None:
    """The model needs the ids in the prompt to be able to cite them."""
    ctx = _ctx(signal_ids=["sig_xyz"], event_ids=["evt_alpha", "evt_beta"])
    llm = _mock_llm(json.dumps(_good_writer_response(["sig_xyz", "evt_alpha"])))
    write_weekly_insight(ctx, backend="claude", llm=llm)
    user_prompt = llm.generate.call_args.kwargs["prompt"]
    assert "sig_xyz" in user_prompt
    assert "evt_alpha" in user_prompt
    assert "evt_beta" in user_prompt


def test_writer_validation_error_message_helpful() -> None:
    """Direct InsightWriter._validate_draft to exercise the typed paths."""
    from src.agents.insight_writer import _DraftPayload

    writer = InsightWriter(llm=MagicMock())
    bad_draft = _DraftPayload(
        sections=[],  # too few
        open_questions=["q"],
        sources_used=[],
    )
    with pytest.raises(InsightWriterError, match="sections must be 4"):
        writer._validate_draft(bad_draft, allowed_ids={"sig_1"})
