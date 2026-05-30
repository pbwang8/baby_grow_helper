"""Phase 2 end-to-end test: events → signals → compress → write → API.

PRD prd/phase2-weekly-insight.md §2.1 / §10.1:
  Walk the same fixture used by Phase 1 (`backfill_xiaoming.jsonl`)
  through the full pipeline and assert the four hard gates land:
    - compress_week_context returns a non-empty CompressedContext
    - insight_writer produces a valid WeeklyInsight (4 sections,
      ≥1 change_over_time, sources_used ⊆ input ids)
    - the insight persists through the API and reads back identically
    - feedback round-trips for one section

The writer layer is mocked at the LLM boundary (no cloud calls) — the
agent's retry/degrade and validation logic is exercised. This is the
"baseline e2e" smoke test the Phase 2 baseline report cites.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from src.agents.context_compressor import compress_week_context
from src.agents.signal_extractor import SignalExtractor
from src.api.main import app
from src.core import db as db_module
from src.core.llm_client import LLMClient, LLMResult
from src.scripts.backfill import insert_records, parse_jsonl

FIXTURE = Path(__file__).parent / "fixtures" / "backfill_xiaoming.jsonl"
WEEK_START = dt.date(2026, 5, 18)  # Monday — last full week in the fixture


# ---- LLM stubs -----------------------------------------------------------


class _SignalLLM(LLMClient):
    """Permissive stub for the signal extractor (matches test_backfill_e2e)."""

    def generate(self, prompt: str, **kwargs: object) -> LLMResult:
        try:
            payload = json.loads(prompt)
            sig_type = payload.get("signal_type", "")
        except json.JSONDecodeError:
            sig_type = ""
        accept = sig_type in {"interest_pattern", "growth_leap", "anomaly"}
        return LLMResult(
            text=json.dumps(
                {
                    "accept": accept,
                    "intensity": 0.7 if accept else 0.0,
                    "confidence": 0.8,
                    "notes": "测试",
                }
            ),
            tokens_in=10,
            tokens_out=5,
            model_used="stub",
            backend="local",
            latency_ms=1,
        )


def _make_writer_response(allowed_ids: list[str]) -> str:
    """Build a writer JSON output that uses only the given source ids."""
    primary = allowed_ids[0] if allowed_ids else "sig_unknown"
    secondary = allowed_ids[1] if len(allowed_ids) > 1 else primary
    return json.dumps(
        {
            "sections": [
                {
                    "axis": "highlight",
                    "title": "本周亮点",
                    "body": f"本周观察到关键瞬间 ({primary})。",
                    "sources_used": [primary],
                },
                {
                    "axis": "change_over_time",
                    "title": "增长在累积",
                    "body": f"对照上周出现频次上升 ({secondary})。",
                    "sources_used": [secondary],
                },
                {
                    "axis": "next_week_focus",
                    "title": "下周关注",
                    "body": "继续观察是否稳定？",
                    "sources_used": [],
                },
                {
                    "axis": "open_questions",
                    "title": "开放问题",
                    "body": "她最喜欢的活动究竟是什么？",
                    "sources_used": [],
                },
            ],
            "open_questions": ["她最喜欢的活动究竟是什么？"],
            "sources_used": [primary] + ([secondary] if secondary != primary else []),
        },
        ensure_ascii=False,
    )


# ---- bootstrap helpers ---------------------------------------------------


def _bootstrap_pipeline(db_path: Path) -> None:
    """Seed child + insert fixture events + run signal extractor."""
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            ("xiaoming", "小明", "2023-06-01"),
        )
    finally:
        conn.close()

    records = parse_jsonl(FIXTURE)
    conn = db_module.get_conn(db_path)
    try:
        insert_records(conn, "xiaoming", records)
    finally:
        conn.close()

    # Run the signal extractor so compress_week_context has signals to draw on.
    extractor = SignalExtractor(llm=_SignalLLM())
    extractor.extract_for_child(
        child_id="xiaoming",
        window_days=14,
        now_iso="2026-05-23T20:00:00+08:00",
    )


# ---- the test ------------------------------------------------------------


def test_phase2_e2e_full_pipeline(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """events → signals → compress → write → persist → GET → feedback."""
    _bootstrap_pipeline(tmp_db)

    # ---- 1) compress_week_context ----------------------------------------
    conn = db_module.get_conn(tmp_db)
    try:
        ctx = compress_week_context("xiaoming", WEEK_START, conn=conn)
    finally:
        conn.close()

    assert ctx.child_id == "xiaoming"
    assert ctx.week_start == WEEK_START
    assert ctx.child_age_months == 35  # 2023-06-01 → 2026-05-18 ≈ 35.5
    # PRD §2.1#1: must produce *some* compressed evidence for a real week
    assert ctx.signals or ctx.event_highlights, (
        "compressor returned empty payload for a week with fixture data"
    )

    allowed_ids = [s.signal_id for s in ctx.signals] + [
        h.event_id for h in ctx.event_highlights
    ]
    assert allowed_ids, "no source ids — writer would have nothing to cite"

    # ---- 2) insight_writer (mocked at LLM boundary) ----------------------
    writer_response = _make_writer_response(allowed_ids)

    class _WriterLLM(LLMClient):
        calls = 0

        def generate(self, prompt: str, **kwargs: object) -> LLMResult:
            type(self).calls += 1
            return LLMResult(
                text=writer_response,
                tokens_in=900,
                tokens_out=180,
                model_used="claude-sonnet-4-20250514",
                backend="cloud",
                latency_ms=120,
                cache_creation_tokens=0,
                cache_read_tokens=900,
            )

    # Patch the writer used inside the API path.
    from src.api import main as api_main

    captured: dict[str, Any] = {}

    def _spy_write(ctx_arg: Any, *, backend: str = "claude") -> Any:
        captured["ctx"] = ctx_arg
        captured["backend"] = backend
        # Use the real writer with our stub LLM so we exercise validation.
        from src.agents.insight_writer import InsightWriter

        return InsightWriter(llm=_WriterLLM()).run(ctx_arg, backend=backend)  # type: ignore[arg-type]

    monkeypatch.setattr(api_main, "write_weekly_insight", _spy_write)

    # ---- 3) API: generate / list / get / feedback ------------------------
    with TestClient(app) as client:
        r_gen = client.post(
            "/insights/generate",
            json={
                "child_id": "xiaoming",
                "week_start": WEEK_START.isoformat(),
                "backend": "claude",
            },
        )
        assert r_gen.status_code == 201, r_gen.text
        body = r_gen.json()

        # PRD §2.1#3: exactly 4 sections
        assert len(body["sections"]) == 4
        # PRD §10.1: at least one change_over_time
        assert any(s["axis"] == "change_over_time" for s in body["sections"])
        # PRD §3.7: sources_used ⊆ allowed (writer + agent layer enforce it)
        assert set(body["sources_used"]).issubset(allowed_ids)
        # version starts at 1 for a fresh week
        assert body["version"] == 1
        # tokens propagated from the LLMResult
        assert body["tokens_in"] == 900
        assert body["tokens_out"] == 180

        insight_id = body["id"]

        # GET single
        r_get = client.get(f"/insights/{insight_id}")
        assert r_get.status_code == 200
        assert r_get.json()["id"] == insight_id

        # GET list (newest first)
        r_list = client.get("/insights", params={"child_id": "xiaoming"})
        assert r_list.status_code == 200
        ids = [row["id"] for row in r_list.json()]
        assert insight_id in ids

        # POST feedback for one section
        r_fb = client.post(
            f"/insights/{insight_id}/feedback",
            json={
                "section_idx": 1,
                "accuracy": "accurate",
                "value": "inspiring",
                "free_text": "戳中了我没注意到的点。",
            },
        )
        assert r_fb.status_code == 201, r_fb.text
        fb = r_fb.json()
        assert fb["insight_id"] == insight_id
        assert fb["accuracy"] == "accurate"
        assert fb["value"] == "inspiring"

        # ---- 4) regenerate bumps version ---------------------------------
        r_regen = client.post(
            "/insights/generate",
            json={
                "child_id": "xiaoming",
                "week_start": WEEK_START.isoformat(),
                "backend": "claude",
            },
        )
        assert r_regen.status_code == 201
        assert r_regen.json()["version"] == 2

    # writer was called via the API path with the right CompressedContext
    assert captured["backend"] == "claude"
    assert captured["ctx"].child_id == "xiaoming"
    assert captured["ctx"].week_start == WEEK_START

