"""LLMClient: backend routing, usage logging, JSON parsing, ping."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from src.core import db as db_module
from src.core.llm_client import (
    LLMClient,
    LLMError,
    _resolve_backend,
    parse_json_strict,
)


def test_resolve_backend_explicit() -> None:
    assert _resolve_backend("local") == "local"
    assert _resolve_backend("cloud") == "cloud"


def test_resolve_backend_auto_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BGH_LLM_BACKEND", "local")
    assert _resolve_backend("auto") == "local"
    monkeypatch.setenv("BGH_LLM_BACKEND", "cloud")
    assert _resolve_backend("auto") == "cloud"


def test_resolve_backend_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BGH_LLM_BACKEND", "neither")
    with pytest.raises(LLMError):
        _resolve_backend("auto")


def test_cloud_backend_disabled_in_phase0() -> None:
    client = LLMClient()
    with pytest.raises(NotImplementedError):
        client.generate("hi", backend="cloud")


@respx.mock
def test_local_generate_happy_path(tmp_db: Path) -> None:
    respx.post("http://127.0.0.1:11434/api/generate").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": '{"summary":"x","type":"observation","domains":["language"],"emotions":[],"context":""}',
                "prompt_eval_count": 42,
                "eval_count": 17,
                "done": True,
            },
        )
    )
    client = LLMClient(ollama_url="http://127.0.0.1:11434", ollama_model="qwen2.5:3b-instruct")
    result = client.generate("test prompt", backend="local", json_mode=True)
    assert result.tokens_in == 42
    assert result.tokens_out == 17
    assert result.backend == "local"
    assert result.model_used == "qwen2.5:3b-instruct"

    # usage_log persisted
    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT backend, model, tokens_in, tokens_out, purpose FROM usage_log"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["backend"] == "local"
    assert row["tokens_in"] == 42
    assert row["tokens_out"] == 17
    assert row["purpose"] == "recorder"


@respx.mock
def test_local_generate_passes_system_and_json_format(tmp_db: Path) -> None:
    route = respx.post("http://127.0.0.1:11434/api/generate").mock(
        return_value=httpx.Response(
            200, json={"response": "{}", "prompt_eval_count": 1, "eval_count": 1}
        )
    )
    client = LLMClient(ollama_url="http://127.0.0.1:11434")
    client.generate("prompt", system="be concise", json_mode=True, backend="local")
    assert route.called
    sent = route.calls.last.request
    body = sent.read().decode("utf-8")
    assert '"system":"be concise"' in body
    assert '"format":"json"' in body
    assert '"stream":false' in body


@respx.mock
def test_local_generate_network_error_raises(tmp_db: Path) -> None:
    respx.post("http://127.0.0.1:11434/api/generate").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    client = LLMClient(ollama_url="http://127.0.0.1:11434")
    with pytest.raises(LLMError, match="Ollama call failed"):
        client.generate("hi", backend="local")


@respx.mock
def test_ping_ollama_true_when_tags_ok() -> None:
    respx.get("http://127.0.0.1:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    assert LLMClient(ollama_url="http://127.0.0.1:11434").ping_ollama() is True


@respx.mock
def test_ping_ollama_false_on_error() -> None:
    respx.get("http://127.0.0.1:11434/api/tags").mock(
        side_effect=httpx.ConnectError("nope")
    )
    assert LLMClient(ollama_url="http://127.0.0.1:11434").ping_ollama() is False


def test_parse_json_strict_plain() -> None:
    assert parse_json_strict('{"a": 1}') == {"a": 1}


def test_parse_json_strict_with_fence() -> None:
    raw = "```json\n{\"a\": 2}\n```"
    assert parse_json_strict(raw) == {"a": 2}


def test_parse_json_strict_rejects_non_object() -> None:
    with pytest.raises(LLMError):
        parse_json_strict("[1, 2, 3]")


def test_parse_json_strict_rejects_garbage() -> None:
    with pytest.raises(LLMError):
        parse_json_strict("not json")
