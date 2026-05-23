"""Single chokepoint for every LLM call in the project.

Why this exists (per CLAUDE.md §4 + ARCHITECTURE.md §6):
  - All token usage must be metered → usage_log
  - Backend choice (local Ollama vs cloud Anthropic) must be a one-line switch
  - No requests.post(...) directly to model endpoints anywhere else

Phase 0 only wires the `local` backend (Ollama). The `cloud` branch raises
NotImplementedError on purpose — Phase 0 PRD §2.2 explicitly forbids cloud
calls during recording to avoid runaway cost. The class shape is set up
so Phase 2 can plug in Anthropic without changing call sites.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Final, Literal

import httpx

from src.core import db as db_module

Backend = Literal["local", "cloud", "auto"]

DEFAULT_OLLAMA_URL: Final[str] = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL: Final[str] = "qwen2.5:3b-instruct"


class LLMError(RuntimeError):
    """Raised when an LLM call fails (network, parse, model offline)."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    model_used: str
    backend: Literal["local", "cloud"]
    latency_ms: int


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
        http_timeout: float = 60.0,
    ) -> None:
        self._ollama_url = (ollama_url or os.environ.get("BGH_OLLAMA_URL") or DEFAULT_OLLAMA_URL).rstrip("/")
        self._ollama_model = (
            ollama_model or os.environ.get("BGH_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
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
    ) -> LLMResult:
        """Run a single prompt and persist usage to usage_log.

        Parameters
        ----------
        prompt : the user-side prompt
        system : optional system prompt
        backend : 'local' | 'cloud' | 'auto' (env-driven)
        purpose : free-form tag persisted to usage_log (recorder|signal|...)
        json_mode : ask Ollama for `format=json` (recorder uses this)
        model : override the configured model id
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
            raise NotImplementedError(
                "Cloud backend is intentionally disabled in Phase 0 (see PRD §2.2)."
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

    # ---- usage log --------------------------------------------------

    def _log_usage(self, *, result: LLMResult, purpose: str) -> None:
        # Logging must never kill the request. If the schema isn't there
        # (e.g. a unit test that doesn't init_db) we just swallow.
        try:
            conn = db_module.get_conn()
        except sqlite3.Error:
            return
        try:
            try:
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
            except sqlite3.Error:
                pass
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
