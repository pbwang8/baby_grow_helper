"""Phase 2 real-cloud smoke — one-shot operator script.

Authorization: prd/inbox/phase2-cloud-smoke-test.md (Cowork 2026-05-31).

What this does:
  1. Loads BGH_ANTHROPIC_API_KEY from .env (gitignored) into the process env.
  2. Spins up an isolated SQLite under $TMPDIR so it never touches the
     working DB.
  3. Seeds child=xiaoming, inserts the synthetic fixture, runs the signal
     extractor with the same permissive stub the e2e uses (we are NOT
     paying the cloud to score signals — only the writer is real).
  4. Calls compress_week_context + InsightWriter against the real Anthropic
     Sonnet 4 endpoint (cache_system=True per PRD §3.3).
  5. Validates the four PRD hard gates (§2.1#3, §10.1, §3.7, plus
     `WeeklyInsight` schema).
  6. Computes USD cost from token counters and aborts if it exceeds the
     authorized $2 ceiling.
  7. Renders reports/phase2-real-snapshot.md with metadata + redacted JSON
     + a side-by-side diff against the mock baseline.

The boundary set by the auth card:
  - Model: claude-sonnet-4 ONLY (we do not switch backends here).
  - Calls: ≤ 2 POST /insights/generate. The writer agent itself can
    internally retry once on validation failure; that counts as part of
    "one POST" for billing-of-record purposes but we surface it in the
    report. We deliberately do NOT loop "/generate" — fully drift outside
    the card.
  - Data: synthetic xiaoming fixture only. Even though the fixture has no
    real names we still run the redactor on the output JSON.
  - Budget: hard $2 USD; expected ≤ $0.10.

Usage:
  uv run python -m src.scripts.cloud_smoke
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
FIXTURE: Path = REPO_ROOT / "tests" / "fixtures" / "backfill_xiaoming.jsonl"
REPORT_PATH: Path = REPO_ROOT / "reports" / "phase2-real-snapshot.md"
ENV_PATH: Path = REPO_ROOT / ".env"

# Authorization card §1
AUTHORIZED_MODEL_PREFIX: str = "claude-sonnet-4"
HARD_BUDGET_USD: float = 2.00
EXPECTED_BUDGET_USD: float = 0.10
WEEK_START: dt.date = dt.date(2026, 5, 18)

# Sonnet 4 list pricing (USD / 1M tokens) as of 2026-05-31.
# Sources: Anthropic public pricing page; cache reads at 0.1x base in.
SONNET4_PRICE_IN_PER_M: float = 3.00
SONNET4_PRICE_OUT_PER_M: float = 15.00
SONNET4_PRICE_CACHE_WRITE_PER_M: float = 3.75  # 1h ephemeral = 1.25x
SONNET4_PRICE_CACHE_READ_PER_M: float = 0.30   # 0.1x


def _load_dotenv(path: Path) -> dict[str, str]:
    """Tiny `.env` loader. Only KEY=VAL lines, ignores comments + blanks.

    We don't add python-dotenv as a runtime dep just for an ops script.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
            os.environ.setdefault(k, v)
    return out


def _redact(payload: object) -> object:
    """Replace anything that looks like a real personal name we know about.

    Even the fixture is synthetic, but the auth card §2 requires us to
    walk the redactor anyway — so the muscle is exercised before we ever
    feed it real data.
    """
    if isinstance(payload, dict):
        return {k: _redact(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_redact(v) for v in payload]
    if isinstance(payload, str):
        s = payload
        # Known children + known adults (names that should NEVER appear in
        # exports). Synthetic fixture has 小明 — we leave that as-is since
        # the report is about a synthetic child; but we redact 瑶瑶/瑶 to
        # prove the redactor catches it if anything leaked.
        for needle in ("瑶瑶", "瑶"):
            s = s.replace(needle, "<REDACTED>")
        return s
    return payload


def _calc_usd(
    *, input_tokens: int, output_tokens: int, cache_creation: int, cache_read: int
) -> float:
    """Per-call USD using Sonnet 4 list price. `input_tokens` is the
    *uncached* portion (Anthropic's bare `input_tokens` field), so we add
    cache write/read separately.
    """
    return (
        input_tokens * SONNET4_PRICE_IN_PER_M / 1_000_000
        + output_tokens * SONNET4_PRICE_OUT_PER_M / 1_000_000
        + cache_creation * SONNET4_PRICE_CACHE_WRITE_PER_M / 1_000_000
        + cache_read * SONNET4_PRICE_CACHE_READ_PER_M / 1_000_000
    )


def _pretty_iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def main() -> int:
    print("=" * 64)
    print(" Phase 2 real-cloud smoke (auth card 2026-05-31)")
    print("=" * 64)

    # ---- 0) Load .env ------------------------------------------------------
    loaded = _load_dotenv(ENV_PATH)
    if loaded:
        keys = ", ".join(sorted(loaded.keys()))
        print(f"[0] loaded .env keys: {keys}")
    api_key = os.environ.get("BGH_ANTHROPIC_API_KEY", "")
    if not api_key:
        print(
            "ERROR: BGH_ANTHROPIC_API_KEY not set. Put it in .env at repo root.",
            file=sys.stderr,
        )
        return 2

    # Force model to Sonnet 4 (auth card §1). Allow override only if the
    # operator explicitly set BGH_ANTHROPIC_MODEL to a sonnet-4 variant.
    model_env = os.environ.get("BGH_ANTHROPIC_MODEL", "")
    if model_env and not model_env.startswith(AUTHORIZED_MODEL_PREFIX):
        print(
            f"ERROR: BGH_ANTHROPIC_MODEL={model_env!r} is outside the "
            f"authorization (must start with {AUTHORIZED_MODEL_PREFIX!r}). "
            f"Unset it or pick a Sonnet 4 variant.",
            file=sys.stderr,
        )
        return 2

    # ---- 1) Isolated DB ---------------------------------------------------
    tmp_root = Path(os.environ.get("TMPDIR", "/tmp")) / "bgh-cloud-smoke"
    tmp_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_root / f"smoke-{int(time.time())}.db"
    os.environ["BGH_DB"] = str(db_path)
    print(f"[1] isolated DB: {db_path}")

    # Imports AFTER env is set so llm_client / db pick up overrides.
    from src.agents.context_compressor import compress_week_context
    from src.agents.insight_writer import InsightWriter, InsightWriterError
    from src.agents.signal_extractor import SignalExtractor
    from src.core import db as db_module
    from src.core.llm_client import LLMClient, LLMResult
    from src.scripts.backfill import insert_records, parse_jsonl

    # Ensure schema is on the new DB.
    db_module.init_db(db_path)

    # ---- 2) Seed child + fixture + signals --------------------------------
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            ("xiaoming", "小明", "2023-06-01"),
        )
        records = parse_jsonl(FIXTURE)
        insert_records(conn, "xiaoming", records)
    finally:
        conn.close()
    print(f"[2] seeded child + {len(records)} fixture events")

    class _SignalLLM(LLMClient):
        """Same permissive stub used in tests. NOT a cloud call."""

        def generate(self, prompt: str, **kwargs: Any) -> LLMResult:
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
                        "notes": "smoke",
                    }
                ),
                tokens_in=0,
                tokens_out=0,
                model_used="stub",
                backend="local",
                latency_ms=0,
            )

    extractor = SignalExtractor(llm=_SignalLLM())
    extractor.extract_for_child(
        child_id="xiaoming",
        window_days=14,
        now_iso="2026-05-23T20:00:00+08:00",
    )

    # ---- 3) Compress ------------------------------------------------------
    conn = db_module.get_conn(db_path)
    try:
        ctx = compress_week_context("xiaoming", WEEK_START, conn=conn)
    finally:
        conn.close()
    allowed_ids = (
        {s.signal_id for s in ctx.signals}
        | {h.event_id for h in ctx.event_highlights}
    )
    print(
        f"[3] compressed week {WEEK_START}: "
        f"signals={len(ctx.signals)} highlights={len(ctx.event_highlights)} "
        f"raw_tokens={ctx.raw_token_count}"
    )
    if not allowed_ids:
        print("ERROR: compressor produced no source ids — nothing to write.", file=sys.stderr)
        return 3

    # ---- 4) REAL cloud call -----------------------------------------------
    # We use the production LLMClient (no stub) — the only guard is that
    # writer-internal retry is single-shot + degrade. We intercept via a
    # thin wrapper to also count "post-level" calls (we authorize ≤2).
    real_calls: list[dict[str, Any]] = []

    class _CountingLLM(LLMClient):
        def generate(self, prompt: str, **kwargs: Any) -> LLMResult:
            t0 = time.perf_counter()
            res = super().generate(prompt, **kwargs)
            wall = (time.perf_counter() - t0) * 1000
            real_calls.append(
                {
                    "model_used": res.model_used,
                    "backend": res.backend,
                    "tokens_in": res.tokens_in,
                    "tokens_out": res.tokens_out,
                    "cache_creation_tokens": res.cache_creation_tokens,
                    "cache_read_tokens": res.cache_read_tokens,
                    "latency_ms": res.latency_ms,
                    "wall_ms_observed": int(wall),
                }
            )
            return res

    print("[4] calling Anthropic Sonnet 4 (this is the only paid step)…")
    t_total = time.perf_counter()
    writer = InsightWriter(llm=_CountingLLM())
    try:
        insight = writer.run(ctx, backend="claude")
    except InsightWriterError as e:
        print(f"ERROR: writer failed/degraded: {e}", file=sys.stderr)
        # Still write a report — Cowork wants the failure mode visible.
        _emit_report(
            insight=None,
            ctx=ctx,
            calls=real_calls,
            wall_total_ms=int((time.perf_counter() - t_total) * 1000),
            failure=str(e),
        )
        return 4
    wall_total_ms = int((time.perf_counter() - t_total) * 1000)

    # ---- 5) Hard gates ----------------------------------------------------
    gates: list[tuple[str, bool, str]] = []
    gates.append(
        (
            "PRD §2.1#3 four sections",
            len(insight.sections) == 4,
            f"got {len(insight.sections)}",
        )
    )
    has_change = any(s.axis == "change_over_time" for s in insight.sections)
    gates.append(("PRD §10.1 ≥1 change_over_time", has_change, ""))
    sources_ok = set(insight.sources_used).issubset(allowed_ids)
    gates.append(
        (
            "PRD §3.7 sources_used ⊆ input ids",
            sources_ok,
            f"unknown={set(insight.sources_used) - allowed_ids}",
        )
    )
    sec_sources_ok = all(
        set(s.sources_used).issubset(allowed_ids) for s in insight.sections
    )
    gates.append(
        ("PRD §3.7 per-section sources ⊆ input ids", sec_sources_ok, "")
    )
    gates.append(
        (
            "model_used starts with claude-sonnet-4",
            insight.model_used.startswith(AUTHORIZED_MODEL_PREFIX)
            or insight.model_used == "degraded",
            f"got {insight.model_used!r}",
        )
    )

    all_pass = all(ok for _, ok, _ in gates)
    print(f"[5] gates: {sum(1 for _, ok, _ in gates if ok)}/{len(gates)} pass")
    for name, ok, hint in gates:
        marker = "✅" if ok else "❌"
        print(f"      {marker} {name}{(' — ' + hint) if hint and not ok else ''}")

    # ---- 6) Cost ----------------------------------------------------------
    total_in = sum(c["tokens_in"] for c in real_calls)
    total_out = sum(c["tokens_out"] for c in real_calls)
    total_cwrite = sum(c["cache_creation_tokens"] for c in real_calls)
    total_cread = sum(c["cache_read_tokens"] for c in real_calls)
    # `tokens_in` from LLMClient already includes cache_creation + cache_read.
    # For pricing we need the bare input portion separately:
    bare_in = total_in - total_cwrite - total_cread
    cost_usd = _calc_usd(
        input_tokens=max(0, bare_in),
        output_tokens=total_out,
        cache_creation=total_cwrite,
        cache_read=total_cread,
    )
    print(
        f"[6] tokens: in_total={total_in} (bare={bare_in}, "
        f"cache_write={total_cwrite}, cache_read={total_cread}), out={total_out}; "
        f"cost ≈ ${cost_usd:.4f}"
    )
    if cost_usd > HARD_BUDGET_USD:
        print(
            f"ERROR: cost ${cost_usd:.4f} > hard budget ${HARD_BUDGET_USD}.",
            file=sys.stderr,
        )
        # still emit report
        _emit_report(
            insight=insight,
            ctx=ctx,
            calls=real_calls,
            wall_total_ms=wall_total_ms,
            cost_usd=cost_usd,
            gates=gates,
            over_budget=True,
        )
        return 5

    # ---- 7) Report --------------------------------------------------------
    _emit_report(
        insight=insight,
        ctx=ctx,
        calls=real_calls,
        wall_total_ms=wall_total_ms,
        cost_usd=cost_usd,
        gates=gates,
    )
    print(f"[7] report written → {REPORT_PATH}")
    if not all_pass:
        return 6
    return 0


# ---- report rendering -----------------------------------------------------

_AXIS_ZH = {
    "highlight": "本周高光",
    "change_over_time": "成长变化",
    "next_week_focus": "下周关注",
    "open_questions": "开放问题",
}


def _emit_report(
    *,
    insight: Any,
    ctx: Any,
    calls: list[dict[str, Any]],
    wall_total_ms: int,
    cost_usd: float | None = None,
    gates: list[tuple[str, bool, str]] | None = None,
    over_budget: bool = False,
    failure: str | None = None,
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Phase 2 — Real-Cloud Smoke Snapshot")
    lines.append("")
    lines.append(
        f"> Generated {_pretty_iso_now()} per "
        f"`prd/inbox/phase2-cloud-smoke-test.md` (Cowork 2026-05-31)."
    )
    lines.append(
        "> One-shot operator run — synthetic xiaoming fixture, "
        f"week_start={WEEK_START.isoformat()} (Monday)."
    )
    lines.append(
        "> This is the *only* sanctioned cloud call against Sonnet 4 before "
        "the A/B Sonnet↔Haiku decision packet."
    )
    lines.append("")
    if failure:
        lines.append(f"> **Outcome: writer raised / degraded.** Reason: `{failure}`")
        lines.append("")

    # ---- §1 metadata ---------------------------------------------------
    lines.append("## 1. Call metadata")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    if insight is not None:
        lines.append(f"| model_used | `{insight.model_used}` |")
        lines.append(f"| backend | `{insight.backend}` |")
        lines.append(f"| version | {insight.version} |")
        lines.append(f"| child_age_months (frozen) | {insight.child_age_months} |")
    lines.append(f"| writer-level LLM calls | {len(calls)} |")
    lines.append(
        f"| total tokens_in (incl. cache) | "
        f"{sum(c['tokens_in'] for c in calls)} |"
    )
    lines.append(
        f"| cache_creation tokens | {sum(c['cache_creation_tokens'] for c in calls)} |"
    )
    lines.append(
        f"| cache_read tokens | {sum(c['cache_read_tokens'] for c in calls)} |"
    )
    lines.append(
        f"| total tokens_out | {sum(c['tokens_out'] for c in calls)} |"
    )
    lines.append(f"| wall-clock (writer.run) | {wall_total_ms} ms |")
    if cost_usd is not None:
        lines.append(
            f"| **estimated USD cost** | **${cost_usd:.4f}** "
            f"({'OVER BUDGET' if over_budget else f'≤ ${HARD_BUDGET_USD}'}) |"
        )
    lines.append("")
    if calls:
        lines.append("Per-call breakdown:")
        lines.append("")
        lines.append(
            "| # | model | backend | in | out | cache_w | cache_r | latency_ms |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, c in enumerate(calls, 1):
            lines.append(
                f"| {i} | `{c['model_used']}` | {c['backend']} | "
                f"{c['tokens_in']} | {c['tokens_out']} | "
                f"{c['cache_creation_tokens']} | {c['cache_read_tokens']} | "
                f"{c['latency_ms']} |"
            )
        lines.append("")

    # ---- §2 redacted JSON ---------------------------------------------
    lines.append("## 2. Redacted insight JSON")
    lines.append("")
    if insight is None:
        lines.append("_writer raised — no JSON to snapshot._")
        lines.append("")
    else:
        # Shape it into the same envelope the API would return, then redact.
        envelope = {
            "id": insight.id,
            "child_id": insight.child_id,
            "week_start": insight.week_start.isoformat(),
            "version": insight.version,
            "child_age_months": insight.child_age_months,
            "backend": insight.backend,
            "model_used": insight.model_used,
            "tokens_in": insight.tokens_in,
            "tokens_out": insight.tokens_out,
            "sections": [
                {
                    "axis": s.axis,
                    "title": s.title,
                    "body": s.body,
                    "sources_used": list(s.sources_used),
                }
                for s in insight.sections
            ],
            "open_questions": list(insight.open_questions),
            "sources_used": list(insight.sources_used),
        }
        # Hex IDs from the fixture path get a stable digest mask so the
        # report is diffable across runs without leaking the exact event id.
        def _mask_ids(obj: object) -> object:
            if isinstance(obj, dict):
                return {k: _mask_ids(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_mask_ids(v) for v in obj]
            if isinstance(obj, str):
                # Mask the random hex tail of evt_/sig_ ids
                return re.sub(
                    r"(evt_[a-z]+_\d{4}_\d{2}_\d{2}_)[0-9a-f]{6}",
                    r"\1<hex>",
                    re.sub(r"(sig_)[0-9a-f]{8,}", r"\1<hex>", obj),
                )
            return obj

        redacted = _mask_ids(_redact(envelope))
        lines.append("```json")
        lines.append(json.dumps(redacted, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    # ---- §3 gates ------------------------------------------------------
    if gates:
        lines.append("## 3. PRD hard-gate verification")
        lines.append("")
        lines.append("| gate | result | note |")
        lines.append("|---|---|---|")
        for name, ok, hint in gates:
            lines.append(f"| {name} | {'✅' if ok else '❌'} | {hint or '—'} |")
        lines.append("")

    # ---- §4 diff vs mock ----------------------------------------------
    lines.append("## 4. Diff vs the mock-LLM baseline (`reports/phase2-baseline.md`)")
    lines.append("")
    lines.append(
        "The e2e under `tests/test_phase2_e2e.py` exercises the same "
        "compressor + writer + API layer with a hand-written stub JSON. "
        "Differences worth flagging when reading this snapshot:"
    )
    lines.append("")
    if insight is not None:
        sec_titles = " / ".join(s.title for s in insight.sections)
        sec_axes = " / ".join(s.axis for s in insight.sections)
        lines.append(f"- **Section titles**: `{sec_titles}`")
        lines.append(f"- **Section axes**: `{sec_axes}`")
        lines.append(
            f"- **change_over_time present**: "
            f"{'yes' if any(s.axis == 'change_over_time' for s in insight.sections) else 'no'}"
        )
        lines.append(
            f"- **sources_used in envelope**: {len(insight.sources_used)} ids "
            f"(allowed pool was {len({s.signal_id for s in ctx.signals}) + len({h.event_id for h in ctx.event_highlights})})"
        )
        lines.append(
            f"- **open_questions count**: {len(insight.open_questions)} "
            f"(PRD bound: 1–3)"
        )
        # Plain-English diff hooks the operator can fill in by reading the
        # JSON above against the mock fixture at tests/test_phase2_e2e.py.
        lines.append("")
        lines.append(
            "Mock baseline produced canned strings (`本周观察到关键瞬间…`, "
            "`对照上周出现频次上升…`); compare the **real** prose above to "
            "judge whether Sonnet 4 actually 'said something a parent would "
            "read to the end'. That subjective read is captured in §5."
        )
        lines.append("")

    # ---- §5 subjective rating placeholder ------------------------------
    lines.append("## 5. One-line subjective rating")
    lines.append("")
    lines.append(
        "_To be filled by Cowork after reading §2._ The auth card's only "
        "mock-exempt signal is whether the insight is 'something a parent "
        "would want to read all the way through'. Anything below "
        "'I'd read this to the end' is a Phase 3 prompt-tuning candidate."
    )
    lines.append("")

    # ---- §6 follow-ups -------------------------------------------------
    lines.append("## 6. Follow-ups to feed back into Phase 3 retro")
    lines.append("")
    lines.append(
        "- A/B Sonnet 4 ↔ Haiku 4.5 (PRD §3.1) — this snapshot is the "
        "'before' card."
    )
    lines.append(
        "- If §3 gates failed: file an Open Question in the auth card §5 "
        "with a one-paragraph reproduction note."
    )
    lines.append(
        "- If cost > $0.10 (auth card §3): re-check `cache_system=True` "
        "wiring; the second call should mostly be cache_read."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "_Generated by `python -m src.scripts.cloud_smoke`. The script "
        "lives at `src/scripts/cloud_smoke.py`; it loads `.env` and isolates "
        "the DB under `$TMPDIR`._"
    )
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
