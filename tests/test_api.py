"""FastAPI end-to-end tests with the recorder mocked out.

Health uses a stub LLMClient that pretends Ollama is up. Event creation uses
a stub Recorder that returns a deterministic StructuredEvent so we test the
HTTP shape and the DB write, not the model.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.agents.recorder import Recorder, StructuredEvent
from src.api.main import app, get_llm_client, get_recorder
from src.core import db as db_module
from src.core.llm_client import LLMClient


class _StubRecorder(Recorder):
    def __init__(self) -> None:
        # don't call super().__init__: skip prompt loading + LLMClient construction
        self._system = "stub"

    def record(  # type: ignore[override]
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
    def ping_ollama(self) -> bool:  # type: ignore[override]
        return True


class _StubLLMDown(LLMClient):
    def ping_ollama(self) -> bool:  # type: ignore[override]
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
