"""Unit tests for src/core/embeddings.py.

We do NOT load BGE here — the real model weighs ~400MB and would slow
the suite to a crawl. A `_StubEmbedder` produces deterministic vectors
so the storage + similarity code path is exercised end-to-end.

The actual BGE model is hit by the integration test in
`tests/test_embeddings_integration.py` (marked `integration`, skipped
under `make test`).
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from src.core import db as db_module
from src.core import embeddings as emb_module
from src.core.embeddings import (
    BGE_SMALL_ZH_DIM,
    Embedder,
    EmbeddingsError,
    SimilarEvent,
    _pack_vector,
    _unpack_vector,
    embed_and_store_event,
    embed_text,
    find_similar_events,
    get_embedder,
    set_embedder,
)

# ---- stub encoder ---------------------------------------------------------


class _StubEmbedder:
    """Deterministic 4-d encoder. Maps Chinese keywords to fixed unit vectors
    so we can craft 'similar' and 'dissimilar' events without any ML."""

    model_name = "stub"
    dim = 4

    def __init__(self) -> None:
        # all unit-norm so cosine distance is 1 - dot
        self.lookup: dict[str, list[float]] = {
            "music": [1.0, 0.0, 0.0, 0.0],
            "motor": [0.0, 1.0, 0.0, 0.0],
            "social": [0.0, 0.0, 1.0, 0.0],
            "diet": [0.0, 0.0, 0.0, 1.0],
        }

    def _vec_for(self, text: str) -> list[float]:
        for k, v in self.lookup.items():
            if k in text:
                return v
        # fall back to a fixed, low-similarity vector
        return [0.5, 0.5, 0.5, 0.5]

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec_for(t) for t in texts]


@pytest.fixture()
def stub_embedder() -> Iterator[_StubEmbedder]:
    """Install the stub for the duration of one test, restore afterward."""
    stub = _StubEmbedder()
    set_embedder(stub)
    try:
        yield stub
    finally:
        set_embedder(None)  # next get_embedder() will lazy-load BGE again


# ---- pack / unpack --------------------------------------------------------


def test_pack_unpack_roundtrip() -> None:
    vec = [0.1, -0.2, 0.3, -0.4]
    blob = _pack_vector(vec)
    assert len(blob) == 4 * 4  # 4 floats × 4 bytes
    back = _unpack_vector(blob)
    for a, b in zip(vec, back, strict=True):
        assert math.isclose(a, b, abs_tol=1e-6)


def test_pack_empty_raises() -> None:
    with pytest.raises(EmbeddingsError):
        _pack_vector([])


def test_unpack_malformed_raises() -> None:
    with pytest.raises(EmbeddingsError):
        _unpack_vector(b"\x00\x00\x00")  # not multiple of 4


# ---- embed_text -----------------------------------------------------------


def test_embed_text_via_stub(stub_embedder: _StubEmbedder) -> None:
    v = embed_text("今天玩了 music")
    assert v == [1.0, 0.0, 0.0, 0.0]


def test_embed_text_rejects_empty(stub_embedder: _StubEmbedder) -> None:
    with pytest.raises(EmbeddingsError):
        embed_text("   ")


# ---- get / set embedder ---------------------------------------------------


def test_set_and_reset_embedder() -> None:
    stub = _StubEmbedder()
    set_embedder(stub)
    assert get_embedder() is stub
    set_embedder(None)
    # Now get_embedder would lazy-construct BGE; we just check the slot is empty
    assert emb_module._embedder is None


# ---- DB persistence -------------------------------------------------------


def _insert_event(conn: object, eid: str, child_id: str, summary: str, ts: str) -> None:
    conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO events (id, child_id, timestamp, raw_text, summary, type,
                            domains_json, emotions_json, context, source, model_used)
        VALUES (?, ?, ?, ?, ?, 'observation', '["other"]', '[]', '', 'manual', 'stub')
        """,
        (eid, child_id, ts, summary, summary),
    )


def test_embed_and_store_event_writes_blob(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_event(conn, "e1", "xiaoming", "music time", "2026-05-15T10:00:00+08:00")
        embed_and_store_event("e1", "music time", conn=conn)
        row = conn.execute(
            "SELECT vector, model FROM event_embeddings WHERE event_id = ?", ("e1",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["model"] == "stub"
    # 4 dims × 4 bytes
    assert len(row["vector"]) == 16
    back = _unpack_vector(row["vector"])
    assert back == [1.0, 0.0, 0.0, 0.0]


def test_embed_and_store_event_idempotent(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_event(conn, "e1", "xiaoming", "music time", "2026-05-15T10:00:00+08:00")
        embed_and_store_event("e1", "music time", conn=conn)
        # second call with different text should overwrite
        embed_and_store_event("e1", "motor leap", conn=conn)
        row = conn.execute(
            "SELECT vector FROM event_embeddings WHERE event_id = ?", ("e1",)
        ).fetchone()
    finally:
        conn.close()
    assert _unpack_vector(row["vector"]) == [0.0, 1.0, 0.0, 0.0]


def test_embed_and_store_event_rejects_unknown_event(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    """FK constraint: event_id must already exist in events."""
    import sqlite3 as _sql

    with pytest.raises(_sql.IntegrityError):
        embed_and_store_event("ghost", "nope")


# ---- similarity -----------------------------------------------------------


def _seed_events_with_embeddings(
    db_path: Path,
    items: list[tuple[str, str, str]],  # (id, summary→keyword, ts)
) -> None:
    conn = db_module.get_conn(db_path)
    try:
        for eid, summary, ts in items:
            _insert_event(conn, eid, "xiaoming", summary, ts)
            embed_and_store_event(eid, summary, conn=conn)
    finally:
        conn.close()


def test_find_similar_orders_by_distance(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    _seed_events_with_embeddings(
        seeded_xiaoming,
        [
            ("e1", "music a", "2026-05-15T10:00:00+08:00"),
            ("e2", "music b", "2026-05-16T10:00:00+08:00"),  # same vec as e1
            ("e3", "motor a", "2026-05-17T10:00:00+08:00"),  # orthogonal
            ("e4", "diet a", "2026-05-18T10:00:00+08:00"),   # orthogonal
        ],
    )
    sims = find_similar_events("e1", k=3)
    assert len(sims) == 3
    # e2 (identical vector) must come first with distance ~0
    assert sims[0].event_id == "e2"
    assert sims[0].distance < 1e-5
    # remaining two are orthogonal to music → distance ≈ 1.0
    for s in sims[1:]:
        assert s.event_id in {"e3", "e4"}
        assert math.isclose(s.distance, 1.0, abs_tol=1e-5)


def test_find_similar_excludes_self(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    _seed_events_with_embeddings(
        seeded_xiaoming,
        [
            ("e1", "music a", "2026-05-15T10:00:00+08:00"),
            ("e2", "music b", "2026-05-16T10:00:00+08:00"),
        ],
    )
    sims = find_similar_events("e1", k=5)
    assert all(s.event_id != "e1" for s in sims)


def test_find_similar_returns_empty_when_no_embedding(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    """Background task hasn't run yet → graceful empty list, no crash."""
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        _insert_event(conn, "e1", "xiaoming", "music a", "2026-05-15T10:00:00+08:00")
    finally:
        conn.close()
    sims = find_similar_events("e1")
    assert sims == []


def test_find_similar_unknown_event_raises(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    with pytest.raises(EmbeddingsError, match="not found"):
        find_similar_events("ghost")


def test_find_similar_zero_k_returns_empty(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    assert find_similar_events("anything", k=0) == []


def test_find_similar_returns_dataclass_shape(
    seeded_xiaoming: Path, stub_embedder: _StubEmbedder
) -> None:
    _seed_events_with_embeddings(
        seeded_xiaoming,
        [
            ("e1", "music a", "2026-05-15T10:00:00+08:00"),
            ("e2", "music b", "2026-05-16T10:00:00+08:00"),
        ],
    )
    sims = find_similar_events("e1", k=1)
    assert isinstance(sims[0], SimilarEvent)
    assert sims[0].summary == "music b"
    assert sims[0].timestamp == "2026-05-16T10:00:00+08:00"


# ---- BGE constants sanity -------------------------------------------------


def test_bge_constants_match_protocol() -> None:
    """Sanity: the Embedder protocol has the same shape we hard-code."""
    # not running the real BGE here, just asserting the module advertises 512.
    assert BGE_SMALL_ZH_DIM == 512
    # The protocol is `runtime_checkable` — the stub satisfies it.
    assert isinstance(_StubEmbedder(), Embedder)


def test_pack_blob_endianness_is_little() -> None:
    """sqlite-vec expects little-endian f32 BLOBs; lock the contract."""
    blob = _pack_vector([1.0])
    # Manually packed via struct '<f' — first byte should be 0x00, last 0x3f
    assert blob == struct.pack("<f", 1.0)
