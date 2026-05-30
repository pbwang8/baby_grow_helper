"""Integration test against a real local Ollama.

Run with:  uv run pytest -m integration

Skipped by default in `make test` so day-to-day testing doesn't require a
running Ollama. This test is the F7 gate from decisions/0001: it samples
all 10 Chinese fixtures and asserts the recorder agrees with each
sample's `expected_type` ≥ 80% of the time. If it falls below 80%, that's
the signal to either fix the prompt, switch to a 7B model, or escalate
back to Cowork.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from src.agents.recorder import Recorder, RecorderError
from src.core.llm_client import LLMClient

from tests.fixtures.recorder_samples import SAMPLES

pytestmark = pytest.mark.integration


def _ollama_up(url: str = "http://127.0.0.1:11434") -> bool:
    try:
        r = httpx.get(f"{url}/api/tags", timeout=2.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True, scope="module")
def _require_ollama() -> None:
    if not _ollama_up():
        pytest.skip("Ollama not running on localhost:11434")


def test_recorder_baseline_accuracy(tmp_db: Path, tmp_path: Path) -> None:
    """Phase 0 baseline (decisions/0001 F7).

    Acceptance: structurally valid output for ≥ 9/10 samples; expected_type
    match for ≥ 8/10. Failures dumped to reports/phase0-baseline.local.json
    for inspection.
    """
    rec = Recorder(llm=LLMClient())
    results: list[dict[str, object]] = []
    valid_count = 0
    type_match_count = 0

    for s in SAMPLES:
        t0 = time.perf_counter()
        try:
            ev = rec.record(child_id="xiaoming", raw_text=s.raw_text)
            valid = True
            type_ok = ev.type in s.expected_type
            domains_ok = (not s.must_include_domains) or any(
                d in s.must_include_domains for d in ev.domains
            )
            results.append(
                {
                    "raw_text": s.raw_text,
                    "expected_type": sorted(s.expected_type),
                    "got_type": ev.type,
                    "domains": ev.domains,
                    "must_include_domains": sorted(s.must_include_domains),
                    "type_match": type_ok,
                    "domains_overlap": domains_ok,
                    "summary": ev.summary,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                }
            )
        except RecorderError as e:
            valid = False
            type_ok = False
            results.append(
                {
                    "raw_text": s.raw_text,
                    "error": str(e),
                    "expected_type": sorted(s.expected_type),
                    "type_match": False,
                }
            )
        valid_count += int(valid)
        type_match_count += int(type_ok)

    # Persist for inspection regardless of pass/fail
    out_path = Path("reports") / "phase0-baseline.local.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "valid_count": valid_count,
                "type_match_count": type_match_count,
                "total": len(SAMPLES),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    assert valid_count >= 9, f"Only {valid_count}/10 produced structurally valid JSON"
    assert type_match_count >= 8, (
        f"Only {type_match_count}/10 matched expected `type`. "
        f"See {out_path}."
    )
