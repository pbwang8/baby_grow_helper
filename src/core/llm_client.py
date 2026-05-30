"""Single chokepoint for every LLM call in the project.

Why this exists (per CLAUDE.md §4 + ARCHITECTURE.md §6):
  - All token usage must be metered → usage_log
  - Backend choice (local Ollama vs cloud Anthropic) must be a one-line switch
  - No requests.post(...) directly to model endpoints anywhere else

Phase 0 only wired `local` (Ollama). Phase 2 enables `cloud`:
  - Anthropic Messages API shape (works against api.anthropic.com OR a
    company proxy that accepts Anthropic-flavored requests, e.g. the
    rednote `runway` Bedrock gateway).
  - **Prompt caching** is a first-class concern (PRD §3.3). The system
    prompt is cached with TTL=1h; expected ~70% input-token savings on
    weekly insight runs.
  - Token accounting comes from `response.usage` and includes the
    cache_creation_input_tokens / cache_read_input_tokens split so the
    `usage_log` row can answer "did this call hit the cache?".

We keep `cloud` calls as non-streaming JSON to fit the existing
synchronous LLMResult shape — Phase 2 doesn't need streaming.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Final, Literal

import httpx

from src.core import db as db_module

logger = logging.getLogger(__name__)

Backend = Literal["local", "cloud", "auto"]

DEFAULT_OLLAMA_URL: Final[str] = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL: Final[str] = "qwen2.5:3b-instruct"

# PRD §3.1: Sonnet for the first 4 weeks, Haiku A/B from week 5.
DEFAULT_ANTHROPIC_BASE_URL: Final[str] = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL: Final[str] = "claude-sonnet-4-20250514"
ANTHROPIC_API_VERSION: Final[str] = "2023-06-01"
ANTHROPIC_DEFAULT_MAX_TOKENS: Final[int] = 1024

# PRD §3.3: prompt cache TTL = 1h (手动调试期更省，长期与 5min 差异不大)
PROMPT_CACHE_TTL: Final[Literal["5m", "1h"]] = "1h"


class LLMError(RuntimeError):
    """Raised when an LLM call fails (network, parse, model offline)."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens_in: int                              # raw input tokens (uncached + cache_creation + cache_read)
    tokens_out: int
    model_used: str
    backend: Literal["local", "cloud"]
    latency_ms: int
    # Phase 2: prompt caching introspection (cloud only, 0 for local)
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


def _resolve_backend(requested: Backend) -> Literal["local", "cloud"]:
    if requested == "auto":
        env = os.environ.get("BGH_LLM_BACKEND", "local").lower()
        if env not in {"local", "cloud"}:
            raise LLMError(f"BGH_LLM_BACKEND must be 'local' or 'cloud', got {env!r}")
        return env  # type: ignore[return-value]
    return requested


class LLMClient:
    """The only path to a model. Records every call to usage_log."""

    def __init__(
        self,
        *,
        ollama_url: str | None = None,
        ollama_model: str | None = None,
        anthropic_base_url: str | None = None,
        anthropic_model: str | None = None,
        anthropic_api_key: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._ollama_url = (ollama_url or os.environ.get("BGH_OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
        self._ollama_model = (
            ollama_model or os.environ.get("BGH_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        )
        # Phase 2: Anthropic Messages API. Empty key is fine until generate(backend="cloud").
        self._anthropic_base_url = (
            anthropic_base_url
            or os.environ.get("BGH_ANTHROPIC_BASE_URL")
            or DEFAULT_ANTHROPIC_BASE_URL
        ).rstrip("/")
        self._anthropic_model = (
            anthropic_model
            or os.environ.get("BGH_ANTHROPIC_MODEL")
            or DEFAULT_ANTHROPIC_MODEL
        )
        self._anthropic_api_key = (
            anthropic_api_key or os.environ.get("BGH_ANTHROPIC_API_KEY") or ""
        )
        self._timeout = http_timeout

    # ---- public API -------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        backend: Backend = "auto",
        purpose: str = "recorder",
        json_mode: bool = False,
        model: str | None = None,
        cache_system: bool = True,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """Run a single prompt and persist usage to usage_log.

        Parameters
        ----------
        prompt : the user-side prompt
        system : optional system prompt
        backend : 'local' | 'cloud' | 'auto' (env-driven)
        purpose : free-form tag persisted to usage_log (recorder|signal|insight|...)
        json_mode : ask Ollama for `format=json` (recorder uses this).
                    Cloud branch ignores this — the writer prompt instructs the
                    model to emit JSON, and we parse defensively.
        model : override the configured model id
        cache_system : Phase 2 — cache the system prompt with TTL=1h. Cloud only;
                       no-op for local.
        max_tokens : output cap for the cloud branch (Anthropic requires it).
        """
        resolved = _resolve_backend(backend)
        if resolved == "local":
            result = self._call_ollama(
                prompt=prompt,
                system=system,
                json_mode=json_mode,
                model=model or self._ollama_model,
            )
        else:
            result = self._call_anthropic(
                prompt=prompt,
                system=system,
                model=model or self._anthropic_model,
                cache_system=cache_system,
                max_tokens=max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS,
            )
        self._log_usage(result=result, purpose=purpose)
        return result

    # ---- backends ---------------------------------------------------

    def _call_ollama(
        self,
        *,
        prompt: str,
        system: str | None,
        json_mode: bool,
        model: str,
    ) -> LLMResult:
        url = f"{self._ollama_url}/api/generate"
        payload: dict[str, object] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        if system is not None:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as e:
            raise LLMError(
                f"Ollama call failed ({type(e).__name__}: {e}). "
                "Is `ollama serve` running and is the model pulled?"
            ) from e
        latency_ms = int((time.perf_counter() - t0) * 1000)

        text = body.get("response", "")
        if not isinstance(text, str):
            raise LLMError(f"Unexpected Ollama response shape: {body!r}")

        tokens_in = int(body.get("prompt_eval_count", 0) or 0)
        tokens_out = int(body.get("eval_count", 0) or 0)
        return LLMResult(
            text=text,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_used=model,
            backend="local",
            latency_ms=latency_ms,
        )

    def _call_anthropic(
        self,
        *,
        prompt: str,
        system: str | None,
        model: str,
        cache_system: bool,
        max_tokens: int,
    ) -> LLMResult:
        """Anthropic Messages API call. Compatible with api.anthropic.com or
        an Anthropic-flavored proxy (e.g. rednote `runway` Bedrock gateway).

        Prompt caching wiring (PRD §3.3):
          The system prompt is wrapped as a single text block carrying
          `cache_control: {type: ephemeral, ttl: 1h}`. The first call within
          a 1-hour window pays full input-token cost and contributes
          `cache_creation_input_tokens`; subsequent calls hit the cache and
          report `cache_read_input_tokens` instead. Both are surfaced on
          LLMResult so the usage_log row tells the operator whether the call
          was warm.
        """
        if not self._anthropic_api_key:
            raise LLMError(
                "Cloud backend requested but BGH_ANTHROPIC_API_KEY is empty. "
                "Set it in your environment (or pass anthropic_api_key=...)."
            )

        url = f"{self._anthropic_base_url}/v1/messages"
        headers = {
            "x-api-key": self._anthropic_api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        # PRD §3.3: extended cache TTL (1h) needs the beta header.
        if cache_system and PROMPT_CACHE_TTL == "1h":
            headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"

        payload: dict[str, object] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            if cache_system:
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {
                            "type": "ephemeral",
                            "ttl": PROMPT_CACHE_TTL,
                        },
                    }
                ]
            else:
                payload["system"] = system

        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as e:
            raise LLMError(
                f"Anthropic call failed ({type(e).__name__}: {e}). "
                f"base_url={self._anthropic_base_url}"
            ) from e
        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Extract text from `content` array — Anthropic returns a list of
        # content blocks. We concatenate text-type blocks; non-streaming
        # responses are usually a single block but defensive code is cheap.
        text = _extract_anthropic_text(body)

        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise LLMError(f"Unexpected Anthropic usage shape: {body!r}")
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
        # tokens_in is the "raw" total (uncached input + cache_creation +
        # cache_read). Anthropic's `input_tokens` excludes the cached portion,
        # so we add the two cache accounts back to keep the usage_log column
        # comparable to Ollama's prompt_eval_count.
        tokens_in_total = input_tokens + cache_creation + cache_read
        return LLMResult(
            text=text,
            tokens_in=tokens_in_total,
            tokens_out=output_tokens,
            model_used=model,
            backend="cloud",
            latency_ms=latency_ms,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
        )

    # ---- usage log --------------------------------------------------

    def _log_usage(self, *, result: LLMResult, purpose: str) -> None:
        # Logging must never kill the request. If the schema isn't there
        # (e.g. a unit test that doesn't init_db) we just swallow.
        try:
            conn = db_module.get_conn()
        except sqlite3.Error:
            return
        try:
            with contextlib.suppress(sqlite3.Error):
                conn.execute(
                    "INSERT INTO usage_log(backend, model, tokens_in, tokens_out, latency_ms, purpose) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        result.backend,
                        result.model_used,
                        result.tokens_in,
                        result.tokens_out,
                        result.latency_ms,
                        purpose,
                    ),
                )
        finally:
            conn.close()

    # ---- introspection ---------------------------------------------

    def ping_ollama(self) -> bool:
        """Cheap reachability check used by /health."""
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(f"{self._ollama_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return isinstance(data, dict)
        except httpx.HTTPError:
            return False


def _extract_anthropic_text(body: dict[str, object]) -> str:
    """Pull text out of Anthropic's `content` array.

    The non-streaming Messages API returns:
        {"content": [{"type": "text", "text": "..."}, ...], "usage": {...}, ...}
    We concatenate all text-type blocks (defensive — the writer prompt's
    JSON should arrive as one block but if a future model splits it we
    don't want to silently drop half).
    """
    content = body.get("content")
    if not isinstance(content, list):
        raise LLMError(f"Unexpected Anthropic response shape: {body!r}")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            t = block.get("text")
            if isinstance(t, str):
                parts.append(t)
    if not parts:
        raise LLMError(f"Anthropic response had no text blocks: {body!r}")
    return "".join(parts)


def parse_json_strict(text: str) -> dict[str, object]:
    """Parse an LLM response that *should* be a JSON object. Raise LLMError otherwise.

    Ollama's `format=json` is reliable but not bulletproof; we also accept the
    common case where the model wraps JSON in code fences.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip ```json ... ``` fence
        cleaned = cleaned.strip("`")
        # the language tag may remain
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model returned non-JSON: {text!r}") from e
    if not isinstance(parsed, dict):
        raise LLMError(f"Model returned JSON but not an object: {parsed!r}")
    return parsed
