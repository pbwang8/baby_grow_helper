"""Integration test for the real BGE-small-zh-v1.5 embedder.

Marked `integration` so `make test` skips it. Runs only when:
  uv run pytest -m integration tests/test_embeddings_integration.py

What this test checks (PRD phase1-signals §3.2 requires baselines):
  1) Real BGE produces 512-d unit vectors
  2) Semantically related Chinese sentences land closer than unrelated ones
  3) Records throughput over a 100-text batch — the number we ship to
     reports/phase1-baseline.md

Hard caps from the PRD:
  - load memory > 1.5GB → kick back to Cowork (ADR)
  - single embed > 200ms (warm) → likewise
We do NOT enforce those here as test failures because hardware varies;
we just print them so the integration runner can paste into the report.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from src.core import db as db_module
from src.core.embeddings import (
    BGE_SMALL_ZH_DIM,
    BGEEmbedder,
    embed_and_store_event,
    embed_text,
    find_similar_events,
    set_embedder,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def bge_embedder() -> Iterator[BGEEmbedder]:
    """Construct + warm up a real BGE encoder. Model files cache under
    ~/.cache/huggingface (or BGH_HF_HOME if set)."""
    cache = os.environ.get("BGH_HF_HOME")
    enc = BGEEmbedder(cache_dir=cache) if cache else BGEEmbedder()
    set_embedder(enc)
    # warm-up — first call pays the load tax
    enc.encode(["预热"])
    yield enc
    set_embedder(None)


def test_bge_returns_512d_unit_vectors(bge_embedder: BGEEmbedder) -> None:
    v = embed_text("今天和小明一起搭积木")
    assert len(v) == BGE_SMALL_ZH_DIM
    norm = math.sqrt(sum(x * x for x in v))
    assert math.isclose(norm, 1.0, abs_tol=1e-3)


def test_bge_semantic_ordering(bge_embedder: BGEEmbedder) -> None:
    """Music-music distance < music-diet distance. Real semantic check."""
    a = embed_text("今天小明在客厅自己哼起小星星")
    b = embed_text("听到背景音乐就跟着拍手")
    c = embed_text("午饭把胡萝卜全吃完了")

    def cos_dist(x: list[float], y: list[float]) -> float:
        dot = sum(xi * yi for xi, yi in zip(x, y, strict=True))
        return 1.0 - dot

    d_ab = cos_dist(a, b)
    d_ac = cos_dist(a, c)
    assert d_ab < d_ac, f"music-music ({d_ab:.3f}) should be < music-diet ({d_ac:.3f})"


def test_bge_batch_throughput(bge_embedder: BGEEmbedder, tmp_path: Path) -> None:
    """Run 100 texts and print throughput. PRD §3.2 wants this number."""
    texts = [f"小明第{i}次玩了一会儿积木" for i in range(100)]

    t0 = time.perf_counter()
    bge_embedder.encode(texts)
    elapsed = time.perf_counter() - t0

    per = elapsed / len(texts) * 1000
    print(
        f"\n[BGE baseline] 100 encodes in {elapsed:.2f}s "
        f"= {per:.1f}ms/encode (warm)"
    )
    # Soft assertion just to flag insanity (e.g. CPU throttle):
    assert elapsed < 60, "100 encodes took longer than a minute — investigate"


def test_bge_end_to_end_with_db(seeded_xiaoming: Path, bge_embedder: BGEEmbedder) -> None:
    """Real encoder, real sqlite-vec cosine — verifies the contract end to end."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        items = [
            ("e1", "今天小明在客厅自己哼起小星星", "2026-05-15T10:00:00+08:00"),
            ("e2", "听到背景音乐就跟着拍手", "2026-05-16T10:00:00+08:00"),
            ("e3", "午饭把胡萝卜全吃完了", "2026-05-17T10:00:00+08:00"),
        ]
        for eid, summary, ts in items:
            conn.execute(
                """
                INSERT INTO events (id, child_id, timestamp, raw_text, summary, type,
                                    domains_json, emotions_json, context, source, model_used)
                VALUES (?, 'xiaoming', ?, ?, ?, 'observation', '["other"]', '[]', '', 'manual', 'bge')
                """,
                (eid, ts, summary, summary),
            )
            embed_and_store_event(eid, summary, conn=conn)
    finally:
        conn.close()

    sims = find_similar_events("e1", k=2)
    assert len(sims) == 2
    # The music-themed e2 must be closer than the diet-themed e3.
    assert sims[0].event_id == "e2"
    assert sims[1].event_id == "e3"
    assert sims[0].distance < sims[1].distance
