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


def test_cloud_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BGH_ANTHROPIC_API_KEY", raising=False)
    client = LLMClient()
    with pytest.raises(LLMError, match="BGH_ANTHROPIC_API_KEY"):
        client.generate("hi", backend="cloud")


@respx.mock
def test_cloud_generate_happy_path(tmp_db: Path) -> None:
    """End-to-end Anthropic call: result text + usage tokens + LLMResult shape."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "{\"ok\":true}"}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 7,
                    "cache_creation_input_tokens": 3500,
                    "cache_read_input_tokens": 0,
                },
            },
        )
    )
    client = LLMClient(anthropic_api_key="sk-test")
    result = client.generate(
        "请生成本周洞察",
        system="You are an early-childhood development analyst.",
        backend="cloud",
        purpose="insight",
    )
    assert route.called
    assert result.backend == "cloud"
    assert result.model_used == "claude-sonnet-4-20250514"
    assert result.text == '{"ok":true}'
    # tokens_in = input_tokens + cache_creation + cache_read
    assert result.tokens_in == 12 + 3500 + 0
    assert result.tokens_out == 7
    assert result.cache_creation_tokens == 3500
    assert result.cache_read_tokens == 0

    # usage_log persisted
    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT backend, model, tokens_in, tokens_out, purpose FROM usage_log"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["backend"] == "cloud"
    assert row["purpose"] == "insight"


@respx.mock
def test_cloud_caches_system_prompt_with_ttl_1h(tmp_db: Path) -> None:
    """PRD §3.3: system prompt is cached with TTL=1h (extended cache beta)."""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 3500,
                },
            },
        )
    )
    client = LLMClient(anthropic_api_key="sk-test")
    client.generate(
        "second call",
        system="LONG SYSTEM PROMPT" * 100,
        backend="cloud",
        purpose="insight",
    )
    assert route.called
    sent = route.calls.last.request

    # Headers: api key + version + extended-cache beta
    assert sent.headers["x-api-key"] == "sk-test"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert sent.headers["anthropic-beta"] == "extended-cache-ttl-2025-04-11"

    # Body: system is a list with cache_control ephemeral 1h
    import json as _json
    body = _json.loads(sent.read().decode("utf-8"))
    assert isinstance(body["system"], list)
    assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["messages"][0] == {"role": "user", "content": "second call"}


@respx.mock
def test_cloud_cache_read_tokens_surface_on_warm_call(tmp_db: Path) -> None:
    """When the cache hits, cache_read_tokens > 0 and input_tokens is small."""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "warm"}],
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 4200,
                },
            },
        )
    )
    client = LLMClient(anthropic_api_key="sk-test")
    result = client.generate("p", system="s", backend="cloud", purpose="insight")
    assert result.cache_read_tokens == 4200
    assert result.cache_creation_tokens == 0
    assert result.tokens_in == 5 + 4200


@respx.mock
def test_cloud_http_error_raises_llm_error(tmp_db: Path) -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            500, json={"type": "error", "error": {"message": "boom"}}
        )
    )
    client = LLMClient(anthropic_api_key="sk-test")
    with pytest.raises(LLMError, match="Anthropic call failed"):
        client.generate("x", backend="cloud")


@respx.mock
def test_cloud_respects_proxy_base_url(tmp_db: Path) -> None:
    """Cloud calls must use BGH_ANTHROPIC_BASE_URL (e.g. rednote runway)."""
    route = respx.post(
        "https://runway.devops.rednote.life/cowork/v1/messages"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        )
    )
    client = LLMClient(
        anthropic_api_key="sk-test",
        anthropic_base_url="https://runway.devops.rednote.life/cowork",
    )
    client.generate("hi", backend="cloud")
    assert route.called


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
