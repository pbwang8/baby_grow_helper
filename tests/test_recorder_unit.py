"""Unit tests for the Recorder validator and LLM-mocked snapshot.

We mock the LLMClient with deterministic JSON so the validator and event
shape are tested without touching Ollama. The integration test (real Ollama)
lives in test_recorder_integration.py and is marked `integration`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from src.agents.recorder import (
    ALLOWED_DOMAINS,
    ALLOWED_EMOTIONS,
    ALLOWED_TYPES,
    Recorder,
    RecorderError,
    StructuredEvent,
    parse_recorder_output,
)
from src.core.llm_client import LLMClient, LLMResult

from tests.fixtures.recorder_samples import SAMPLES, RecorderSample

# ---- parse_recorder_output -------------------------------------------


def _good_payload() -> dict[str, object]:
    return {
        "summary": "首次自主如厕成功",
        "type": "milestone",
        "domains": ["self_care", "independence"],
        "emotions": ["proud", "excited"],
        "context": "家中，家长在场",
    }


def test_parse_happy_path() -> None:
    ev = parse_recorder_output(
        _good_payload(),
        child_id="xiaoming",
        raw_text="原文",
        timestamp="2026-05-19T20:00:00+08:00",
        model_used="qwen2.5:3b-instruct",
    )
    assert ev.type == "milestone"
    assert ev.domains == ["self_care", "independence"]
    assert ev.emotions == ["proud", "excited"]
    assert ev.context.startswith("家中")
    assert ev.id.startswith("evt_2026_05_19_")


def test_parse_rejects_unknown_type() -> None:
    payload = _good_payload() | {"type": "achievement"}
    with pytest.raises(RecorderError, match="`type`"):
        parse_recorder_output(
            payload,
            child_id="xiaoming",
            raw_text="x",
            timestamp="2026-05-19T20:00:00+08:00",
            model_used="m",
        )


def test_parse_rejects_unknown_domain() -> None:
    payload = _good_payload() | {"domains": ["language", "telekinesis"]}
    with pytest.raises(RecorderError, match="closed set"):
        parse_recorder_output(
            payload,
            child_id="xiaoming",
            raw_text="x",
            timestamp="2026-05-19T20:00:00+08:00",
            model_used="m",
        )


def test_parse_requires_at_least_one_domain() -> None:
    payload = _good_payload() | {"domains": []}
    with pytest.raises(RecorderError, match="at least one"):
        parse_recorder_output(
            payload,
            child_id="xiaoming",
            raw_text="x",
            timestamp="2026-05-19T20:00:00+08:00",
            model_used="m",
        )


def test_parse_dedupes_and_caps_domains() -> None:
    payload = _good_payload() | {
        "domains": ["self_care", "self_care", "independence", "motor", "cognition"]
    }
    ev = parse_recorder_output(
        payload,
        child_id="xiaoming",
        raw_text="x",
        timestamp="2026-05-19T20:00:00+08:00",
        model_used="m",
    )
    assert ev.domains == ["self_care", "independence", "motor"]
    assert len(ev.domains) <= 3


def test_parse_empty_summary_rejected() -> None:
    payload = _good_payload() | {"summary": "   "}
    with pytest.raises(RecorderError, match="summary"):
        parse_recorder_output(
            payload,
            child_id="xiaoming",
            raw_text="x",
            timestamp="2026-05-19T20:00:00+08:00",
            model_used="m",
        )


def test_closed_sets_match_prompt() -> None:
    """Defensive: the prompt and the validator must agree on closed sets.

    If the prompt grows a new domain, this test won't catch it automatically
    (Markdown is hard to parse), but a glance here and at recorder.md
    is enough to keep them in sync. We at least assert the validator's
    sets are non-empty and self-consistent.
    """
    assert "milestone" in ALLOWED_TYPES
    assert "self_care" in ALLOWED_DOMAINS
    assert "proud" in ALLOWED_EMOTIONS


# ---- Recorder w/ mocked LLM ------------------------------------------


class _StubLLM(LLMClient):
    """LLMClient subclass that returns canned JSON without any HTTP."""

    def __init__(self, payloads: Iterator[str]) -> None:
        super().__init__()
        self._payloads = payloads

    def generate(self, prompt: str, **kwargs: object) -> LLMResult:
        return LLMResult(
            text=next(self._payloads),
            tokens_in=10,
            tokens_out=20,
            model_used="stub-model",
            backend="local",
            latency_ms=1,
        )


def _canned_for(sample: RecorderSample) -> str:
    """Build a deterministic JSON answer for a sample. Used in snapshot tests."""
    domains = sorted(sample.must_include_domains) or ["other"]
    return json.dumps(
        {
            "summary": "测试摘要",
            "type": sample.primary_type,
            "domains": domains,
            "emotions": [],
            "context": "",
        },
        ensure_ascii=False,
    )


def test_recorder_returns_structured_event(tmp_db: Path) -> None:
    payload = json.dumps(
        {
            "summary": "首次自主如厕成功",
            "type": "milestone",
            "domains": ["self_care", "independence"],
            "emotions": ["proud", "excited"],
            "context": "家中",
        },
        ensure_ascii=False,
    )
    rec = Recorder(llm=_StubLLM(iter([payload])))
    ev = rec.record(child_id="xiaoming", raw_text="今天小明第一次自己尿尿了")
    assert isinstance(ev, StructuredEvent)
    assert ev.summary == "首次自主如厕成功"
    assert ev.child_id == "xiaoming"
    assert ev.model_used == "stub-model"


def test_recorder_rejects_empty_text() -> None:
    rec = Recorder(llm=_StubLLM(iter([])))
    with pytest.raises(RecorderError, match="must not be empty"):
        rec.record(child_id="xiaoming", raw_text="")


def test_recorder_passes_through_validation_failure() -> None:
    bad = json.dumps({"summary": "x", "type": "weird", "domains": ["language"]})
    rec = Recorder(llm=_StubLLM(iter([bad])))
    with pytest.raises(RecorderError, match="`type`"):
        rec.record(child_id="xiaoming", raw_text="今天怎么怎么")


def test_recorder_snapshot_against_10_samples() -> None:
    """With deterministic canned LLM output, parsing all 10 samples must
    produce a stable shape. Inline expected list — if a refactor changes
    any of these fields silently, this assertion blocks it."""
    payloads = iter([_canned_for(s) for s in SAMPLES])
    rec = Recorder(llm=_StubLLM(payloads))

    expected: list[dict[str, object]] = []
    actual: list[dict[str, object]] = []
    for s in SAMPLES:
        ev = rec.record(
            child_id="xiaoming",
            raw_text=s.raw_text,
            timestamp="2026-05-19T20:00:00+08:00",
        )
        actual.append(
            {
                "raw_text": ev.raw_text,
                "summary": ev.summary,
                "type": ev.type,
                "domains": ev.domains,
                "emotions": ev.emotions,
                "context": ev.context,
                "child_id": ev.child_id,
                "model_used": ev.model_used,
                "timestamp": ev.timestamp,
            }
        )
        expected.append(
            {
                "raw_text": s.raw_text,
                "summary": "测试摘要",
                "type": s.primary_type,
                "domains": sorted(s.must_include_domains) or ["other"],
                "emotions": [],
                "context": "",
                "child_id": "xiaoming",
                "model_used": "stub-model",
                "timestamp": "2026-05-19T20:00:00+08:00",
            }
        )
    assert actual == expected


def test_recorder_all_samples_pass_validation_with_canned_payload() -> None:
    """Each sample's expected_type must be a valid type, and any required
    domain must be a real domain. Catches typos in the fixtures themselves."""
    for s in SAMPLES:
        for t in s.expected_type:
            assert t in ALLOWED_TYPES, (s, t)
        for d in s.must_include_domains:
            assert d in ALLOWED_DOMAINS, (s, d)


def test_recorder_id_is_unique_across_calls(tmp_db: Path) -> None:
    payload = json.dumps(
        {
            "summary": "x",
            "type": "observation",
            "domains": ["language"],
            "emotions": [],
            "context": "",
        }
    )
    rec = Recorder(llm=_StubLLM(iter([payload, payload])))
    a = rec.record(
        child_id="xiaoming",
        raw_text="一次",
        timestamp="2026-05-19T20:00:00+08:00",
    )
    b = rec.record(
        child_id="xiaoming",
        raw_text="两次",
        timestamp="2026-05-19T20:00:00+08:00",
    )
    assert a.id != b.id, "ids must collide-avoid"
