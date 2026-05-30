"""FastAPI end-to-end tests with the recorder mocked out.

Health uses a stub LLMClient that pretends Ollama is up. Event creation uses
a stub Recorder that returns a deterministic StructuredEvent so we test the
HTTP shape and the DB write, not the model.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from src.agents.recorder import Recorder, StructuredEvent
from src.agents.signal_extractor import SignalExtractorError
from src.api.main import app, get_llm_client, get_recorder, get_signal_extractor
from src.core import db as db_module
from src.core import embeddings as emb_module
from src.core.llm_client import LLMClient
from src.core.models import Signal


class _StubEmbedder:
    model_name = "stub"
    dim = 4

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.fixture(autouse=True)
def _stub_embedder() -> Iterator[None]:
    """API tests trigger BackgroundTasks → embed_and_store_event. Use a stub
    so we don't try to download the real BGE weights from a unit test."""
    emb_module.set_embedder(_StubEmbedder())
    try:
        yield
    finally:
        emb_module.set_embedder(None)


class _StubRecorder(Recorder):
    def __init__(self) -> None:
        # don't call super().__init__: skip prompt loading + LLMClient construction
        self._system = "stub"

    def record(
        self, *, child_id: str, raw_text: str, timestamp: str | None = None
    ) -> StructuredEvent:
        return StructuredEvent(
            id="evt_test_fixed_1",
            child_id=child_id,
            timestamp=timestamp or "2026-05-19T10:00:00+08:00",
            raw_text=raw_text,
            summary="测试摘要",
            type="milestone",
            domains=["self_care"],
            emotions=["proud"],
            context="测试",
            model_used="stub-model",
        )


class _StubLLMHealthy(LLMClient):
    def ping_ollama(self) -> bool:
        return True


class _StubLLMDown(LLMClient):
    def ping_ollama(self) -> bool:
        return False


def _client_with_recorder(seeded_xiaoming: Path, *, ollama_up: bool = True) -> TestClient:
    app.dependency_overrides[get_recorder] = lambda: _StubRecorder()
    app.dependency_overrides[get_llm_client] = (
        (lambda: _StubLLMHealthy()) if ollama_up else (lambda: _StubLLMDown())
    )
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_health_all_green(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body == {"ok": True, "sqlite": True, "ollama": True}


def test_health_ollama_down(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming, ollama_up=False) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["sqlite"] is True
        assert body["ollama"] is False


def test_post_event_persists_and_returns(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.post(
            "/events",
            json={"child_id": "xiaoming", "raw_text": "今天小明第一次自己尿尿了"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["child_id"] == "xiaoming"
        assert body["type"] == "milestone"
        assert body["domains"] == ["self_care"]

    conn = db_module.get_conn(seeded_xiaoming)
    try:
        rows = conn.execute(
            "SELECT id, child_id, type FROM events WHERE child_id = 'xiaoming'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["type"] == "milestone"


def test_post_event_triggers_background_embedding(seeded_xiaoming: Path) -> None:
    """POST /events should kick off the embedding BackgroundTask, which
    inserts into event_embeddings with the stubbed encoder."""
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.post(
            "/events",
            json={"child_id": "xiaoming", "raw_text": "今天玩了一会音乐"},
        )
        assert r.status_code == 201, r.text
        event_id = r.json()["id"]

    # FastAPI runs background tasks after the response is sent — the
    # `with TestClient` block flushes them on exit.
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        row = conn.execute(
            "SELECT model FROM event_embeddings WHERE event_id = ?", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["model"] == "stub"


def test_post_event_unknown_child_returns_404(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.post(
            "/events",
            json={"child_id": "ghost", "raw_text": "随便"},
        )
        assert r.status_code == 404
        assert "ghost" in r.json()["detail"]


def test_post_event_validation(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.post("/events", json={"child_id": "xiaoming", "raw_text": ""})
        assert r.status_code == 422


def test_get_events_returns_newest_first(seeded_xiaoming: Path) -> None:
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        rows = [
            ("evt_a", "xiaoming", "2026-05-18T10:00:00+08:00", "原文A", "A"),
            ("evt_b", "xiaoming", "2026-05-19T10:00:00+08:00", "原文B", "B"),
        ]
        for rid, child, ts, raw, summary in rows:
            conn.execute(
                """
                INSERT INTO events (id, child_id, timestamp, raw_text, summary, type,
                                    domains_json, emotions_json, source)
                VALUES (?, ?, ?, ?, ?, 'observation', '[]', '[]', 'manual')
                """,
                (rid, child, ts, raw, summary),
            )
    finally:
        conn.close()

    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/events", params={"child_id": "xiaoming", "limit": 10})
        assert r.status_code == 200
        body = r.json()
        assert [e["id"] for e in body] == ["evt_b", "evt_a"]


def test_get_events_limit_validation(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/events", params={"child_id": "xiaoming", "limit": 9999})
        assert r.status_code == 422


# ---- Phase 1: signals + heatmap routes ------------------------------------


def _seed_signal(
    seeded_xiaoming: Path,
    *,
    sig_id: str,
    signal_type: str = "interest_pattern",
    domains: list[str] | None = None,
    intensity: float = 0.7,
    age_months: int = 35,
    status: str = "active",
    evidence: list[str] | None = None,
    last_seen: str = "2026-05-19T10:00:00+08:00",
) -> None:
    sig = Signal(
        id=sig_id,
        child_id="xiaoming",
        signal_type=signal_type,  # type: ignore[arg-type]
        domains=domains or ["music"],
        intensity=intensity,
        child_age_months=age_months,
        confidence=0.85,
        first_seen_at="2026-05-15T10:00:00+08:00",
        last_seen_at=last_seen,
        evidence_event_ids=evidence or ["evt_a", "evt_b"],
        status=status,  # type: ignore[arg-type]
        notes="测试",
    )
    row = sig.as_row()
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row)
    conn = db_module.get_conn(seeded_xiaoming)
    try:
        conn.execute(f"INSERT INTO signals ({cols}) VALUES ({placeholders})", row)
        conn.commit()
    finally:
        conn.close()


def test_get_signals_lists_active(seeded_xiaoming: Path) -> None:
    _seed_signal(
        seeded_xiaoming,
        sig_id="sig_20260519_001",
        last_seen="2026-05-15T10:00:00+08:00",
    )
    _seed_signal(
        seeded_xiaoming,
        sig_id="sig_20260519_002",
        signal_type="growth_leap",
        domains=["self_care", "independence"],
        last_seen="2026-05-19T10:00:00+08:00",
    )
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/signals", params={"child_id": "xiaoming"})
        assert r.status_code == 200, r.text
        body = r.json()
        # newest last_seen first
        assert [s["id"] for s in body] == ["sig_20260519_002", "sig_20260519_001"]
        assert body[0]["signal_type"] == "growth_leap"
        assert body[0]["status"] == "active"


def test_get_signals_status_filter(seeded_xiaoming: Path) -> None:
    _seed_signal(seeded_xiaoming, sig_id="sig_a", status="active")
    _seed_signal(seeded_xiaoming, sig_id="sig_d", status="dismissed")
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get(
            "/signals", params={"child_id": "xiaoming", "status": "dismissed"}
        )
        assert r.status_code == 200
        body = r.json()
        assert [s["id"] for s in body] == ["sig_d"]


def test_get_signals_empty_for_unknown_child(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/signals", params={"child_id": "ghost"})
        assert r.status_code == 200
        assert r.json() == []


class _FakeExtractorOK:
    """Returns one canned signal regardless of input."""

    def extract_for_child(
        self, *, child_id: str, window_days: int = 14, now_iso: str | None = None
    ) -> list[Signal]:
        return [
            Signal(
                id="sig_20260519_999",
                child_id=child_id,
                signal_type="interest_pattern",
                domains=["music"],
                intensity=0.7,
                child_age_months=35,
                confidence=0.85,
                first_seen_at="2026-05-15T10:00:00+08:00",
                last_seen_at="2026-05-19T10:00:00+08:00",
                evidence_event_ids=["e1", "e2", "e3"],
                status="active",
                notes="stub",
            )
        ]


class _FakeExtractor404:
    def extract_for_child(self, **_: object) -> list[Signal]:
        raise SignalExtractorError("child_id='ghost' not found")


class _FakeExtractor502:
    def extract_for_child(self, **_: object) -> list[Signal]:
        raise SignalExtractorError("LLM timed out")


def test_post_signals_extract_returns_signals(seeded_xiaoming: Path) -> None:
    app.dependency_overrides[get_signal_extractor] = lambda: _FakeExtractorOK()
    try:
        with _client_with_recorder(seeded_xiaoming) as client:
            r = client.post(
                "/signals/extract",
                params={"child_id": "xiaoming", "window_days": 14},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert len(body) == 1
            assert body[0]["signal_type"] == "interest_pattern"
            assert body[0]["domains"] == ["music"]
    finally:
        app.dependency_overrides.pop(get_signal_extractor, None)


def test_post_signals_extract_unknown_child_404(seeded_xiaoming: Path) -> None:
    app.dependency_overrides[get_signal_extractor] = lambda: _FakeExtractor404()
    try:
        with _client_with_recorder(seeded_xiaoming) as client:
            r = client.post("/signals/extract", params={"child_id": "ghost"})
            assert r.status_code == 404
            assert "ghost" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_signal_extractor, None)


def test_post_signals_extract_llm_failure_502(seeded_xiaoming: Path) -> None:
    app.dependency_overrides[get_signal_extractor] = lambda: _FakeExtractor502()
    try:
        with _client_with_recorder(seeded_xiaoming) as client:
            r = client.post("/signals/extract", params={"child_id": "xiaoming"})
            assert r.status_code == 502
            assert "LLM" in r.json()["detail"] or "timed out" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_signal_extractor, None)


def _insert_event_row(
    seeded_xiaoming: Path,
    *,
    eid: str,
    ts: str,
    domains: list[str],
) -> None:
    import json as _json

    conn = db_module.get_conn(seeded_xiaoming)
    try:
        conn.execute(
            """
            INSERT INTO events
              (id, child_id, timestamp, raw_text, summary, type,
               domains_json, emotions_json, context, source, model_used)
            VALUES (?, 'xiaoming', ?, '原文', '摘要', 'observation',
                    ?, '[]', '', 'manual', 'stub')
            """,
            (eid, ts, _json.dumps(domains, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def test_get_heatmap_buckets_by_age(seeded_xiaoming: Path) -> None:
    # birthday 2023-06-01 → age at 2026-05-15 is 35 months
    _insert_event_row(
        seeded_xiaoming,
        eid="evt_h1",
        ts="2026-05-15T10:00:00+08:00",
        domains=["music"],
    )
    _insert_event_row(
        seeded_xiaoming,
        eid="evt_h2",
        ts="2026-05-17T10:00:00+08:00",
        domains=["music"],
    )
    # 2025-12-15 → age 30 months
    _insert_event_row(
        seeded_xiaoming,
        eid="evt_h3",
        ts="2025-12-15T10:00:00+08:00",
        domains=["motor"],
    )
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/heatmap", params={"child_id": "xiaoming"})
        assert r.status_code == 200, r.text
        cells = r.json()
        # Two buckets: (30, motor) and (35, music)
        keys = {(c["age_months"], c["domain"]) for c in cells}
        assert (35, "music") in keys
        assert (30, "motor") in keys
        # intensity is normalized
        for c in cells:
            assert 0.0 <= c["intensity"] <= 1.0


def test_get_heatmap_empty_for_unknown_child(seeded_xiaoming: Path) -> None:
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get("/heatmap", params={"child_id": "ghost"})
        assert r.status_code == 200
        assert r.json() == []


def test_get_heatmap_domain_filter(seeded_xiaoming: Path) -> None:
    _insert_event_row(
        seeded_xiaoming,
        eid="evt_m",
        ts="2026-05-15T10:00:00+08:00",
        domains=["music"],
    )
    _insert_event_row(
        seeded_xiaoming,
        eid="evt_x",
        ts="2026-05-15T10:00:00+08:00",
        domains=["motor"],
    )
    with _client_with_recorder(seeded_xiaoming) as client:
        r = client.get(
            "/heatmap", params={"child_id": "xiaoming", "domains": ["music"]}
        )
        assert r.status_code == 200
        cells = r.json()
        assert all(c["domain"] == "music" for c in cells)
        assert len(cells) == 1
