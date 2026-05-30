"""Phase 1 — embeddings & similarity over events.

Per `prd/phase1-signals.md` §2.1#3:
  - Model: BGE-small-zh-v1.5 (locked by PRD; do NOT swap without ADR)
  - Hot path: events get an embedding asynchronously after they land
  - Two primitives this module exposes:
        embed_text(text)               → list[float]
        find_similar_events(event_id)  → list[Event-ish dicts]
    plus a third for the API write path:
        embed_and_store_event(event_id, text)

Design notes (so future me doesn't re-derive these):

1) **Encoder is injectable.** The real `BGEEmbedder` lazy-imports
   `sentence-transformers`, which pulls in torch and ~400MB of weights —
   we don't want every unit test to pay that. Tests pass a deterministic
   stub via `set_embedder()`. The default factory only loads BGE when
   first asked for an embedding.

2) **Storage shape is the existing BLOB column** in `event_embeddings`,
   NOT a sqlite-vec virtual table. The Phase 0 schema reserved
   `vector BLOB`; we pack vectors as little-endian float32 bytes. Reads
   use sqlite-vec's `vec_distance_cosine(blob, blob)` which works on
   raw f32 BLOBs of equal length.

   Rationale: introducing a `vec0` virtual table now would mean a real
   migration (we have the index and FK contract baked in). The cosine
   function over raw BLOBs is `O(n)` per query but n is in the hundreds
   for the foreseeable future — premature to optimize.

3) **Foreign key trust.** `embed_and_store_event` does not validate
   the event_id existence; the caller (the POST /events handler) just
   inserted it within the same transaction. SQLite's FK on
   `event_embeddings(event_id)` will reject zombies anyway.

4) **`find_similar_events` excludes the query event itself** (`!=` filter),
   and limits across the same child_id. Cross-child similarity has no
   product meaning in Phase 1.
"""

from __future__ import annotations

import logging
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from src.core import db as db_module

logger = logging.getLogger(__name__)

# PRD §2.1#3 + §3.2: locked model. Changing requires an ADR.
DEFAULT_MODEL_NAME: Final[str] = "BAAI/bge-small-zh-v1.5"

# BGE-small-zh-v1.5 is 512-d. We freeze this so tests can sanity-check
# without instantiating the real encoder.
BGE_SMALL_ZH_DIM: Final[int] = 512


class EmbeddingsError(RuntimeError):
    """Raised when embedding generation or storage fails."""


# ---- protocol --------------------------------------------------------------


@runtime_checkable
class Embedder(Protocol):
    """Minimal interface the rest of the module needs."""

    model_name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding per input text. May normalize internally."""
        ...


# ---- real BGE backend ------------------------------------------------------


class BGEEmbedder:
    """Default `Embedder` implementation, backed by sentence-transformers.

    Lazy-loaded: the model is downloaded + held in memory only after the
    first `encode()` call. Subsequent calls reuse the same instance.

    The `BGH_HF_HOME` env var lets you redirect HuggingFace's cache to a
    project-local path (default behaviour: ~/.cache/huggingface). Useful
    when you want to keep weights co-located with the project on a
    laptop with a small home partition.
    """

    model_name: str = DEFAULT_MODEL_NAME
    dim: int = BGE_SMALL_ZH_DIM

    def __init__(self, model_name: str | None = None, cache_dir: Path | str | None = None) -> None:
        self.model_name = model_name or DEFAULT_MODEL_NAME
        self._cache_dir = Path(cache_dir).expanduser() if cache_dir else None
        self._model: object | None = None  # lazy

    def _load(self) -> object:
        if self._model is not None:
            return self._model
        try:
            # Local import on purpose — keeps unit tests lightweight.
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - environment-dependent
            raise EmbeddingsError(
                "sentence-transformers not installed. Add the [ml] extra "
                "or `pip install sentence-transformers` to enable BGE."
            ) from e
        kwargs: dict[str, object] = {}
        if self._cache_dir is not None:
            kwargs["cache_folder"] = str(self._cache_dir)
        self._model = SentenceTransformer(self.model_name, **kwargs)
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        # `normalize_embeddings=True` makes cosine = dot, which keeps
        # downstream sqlite-vec queries cheap and consistent.
        vectors = model.encode(  # type: ignore[attr-defined]
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vectors]


# ---- module-level injectable singleton ------------------------------------

_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Return the active embedder, constructing the default lazily."""
    global _embedder
    if _embedder is None:
        _embedder = BGEEmbedder()
    return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Inject (or reset) the active embedder. Tests use this."""
    global _embedder
    _embedder = embedder


# ---- public primitives ----------------------------------------------------


def embed_text(text: str) -> list[float]:
    """Return a single embedding."""
    text = (text or "").strip()
    if not text:
        raise EmbeddingsError("embed_text: empty string")
    out = get_embedder().encode([text])
    if not out:
        raise EmbeddingsError("encoder returned no vector")
    return out[0]


def embed_and_store_event(event_id: str, text: str, *, conn: sqlite3.Connection | None = None) -> None:
    """Compute the embedding for a freshly-recorded event and persist it.

    Idempotent: a second call for the same event_id replaces the row
    (the event's text could have been edited). FK enforces that the
    event_id must already exist in `events`.

    The optional `conn` lets tests use the same connection they prepared
    fixtures on. Production callers should let this open its own.
    """
    vec = embed_text(text)
    blob = _pack_vector(vec)
    model_name = get_embedder().model_name

    own_conn = conn is None
    if conn is None:
        conn = db_module.get_conn()
    try:
        with db_module.transactional(conn):
            conn.execute(
                """
                INSERT INTO event_embeddings (event_id, vector, model)
                VALUES (?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    vector = excluded.vector,
                    model  = excluded.model
                """,
                (event_id, blob, model_name),
            )
    finally:
        if own_conn:
            conn.close()


@dataclass(frozen=True)
class SimilarEvent:
    """Slim shape returned by `find_similar_events` — just enough for callers
    that want to render or aggregate without round-tripping back to events."""

    event_id: str
    distance: float
    summary: str
    timestamp: str


def find_similar_events(
    event_id: str,
    *,
    k: int = 5,
    conn: sqlite3.Connection | None = None,
) -> list[SimilarEvent]:
    """Return the k nearest events to `event_id`, same child, excluding itself.

    Uses cosine *distance* (lower = more similar). If the query event
    has no embedding yet (background task hasn't run), returns [].
    """
    if k <= 0:
        return []
    own_conn = conn is None
    if conn is None:
        conn = db_module.get_conn()
    try:
        row = conn.execute(
            """
            SELECT e.child_id, ee.vector
            FROM events e
            LEFT JOIN event_embeddings ee ON ee.event_id = e.id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise EmbeddingsError(f"event_id={event_id!r} not found")
        if row["vector"] is None:
            # embedding hasn't been computed yet (BackgroundTasks queued
            # but not finished). Caller can retry; we don't block.
            logger.info("similarity miss: %s has no embedding yet", event_id)
            return []

        rows = conn.execute(
            """
            SELECT
                ee.event_id           AS event_id,
                vec_distance_cosine(ee.vector, ?) AS distance,
                e.summary             AS summary,
                e.timestamp           AS timestamp
            FROM event_embeddings ee
            JOIN events e ON e.id = ee.event_id
            WHERE e.child_id = ?
              AND ee.event_id != ?
              AND ee.vector IS NOT NULL
            ORDER BY distance ASC
            LIMIT ?
            """,
            (row["vector"], row["child_id"], event_id, k),
        ).fetchall()
    finally:
        if own_conn:
            conn.close()
    return [
        SimilarEvent(
            event_id=str(r["event_id"]),
            distance=float(r["distance"]),
            summary=str(r["summary"]),
            timestamp=str(r["timestamp"]),
        )
        for r in rows
    ]


# ---- helpers --------------------------------------------------------------


def _pack_vector(vec: Sequence[float]) -> bytes:
    """Pack a vector as little-endian float32 bytes for sqlite-vec.

    sqlite-vec's `vec_distance_cosine` consumes raw f32 BLOBs of equal
    length. Using struct keeps us free of a numpy dependency in the core.
    """
    if not vec:
        raise EmbeddingsError("cannot pack empty vector")
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> list[float]:
    """Inverse of `_pack_vector`. Used by tests / debugging only."""
    if not blob or len(blob) % 4 != 0:
        raise EmbeddingsError(f"malformed vector blob, len={len(blob)}")
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))
