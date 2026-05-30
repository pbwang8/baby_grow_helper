"""Recorder Agent v0.

Maps a parent's free-text observation → structured event row, using a local
small model (Qwen2.5-3B by default) via LLMClient. Output schema mirrors
ARCHITECTURE.md §3.1.

Failure policy (PRD §2.1 #4):
  - Local model unreachable / non-JSON output → fail-fast (raise).
  - No silent cloud fallback in Phase 0.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from src.core.llm_client import LLMClient, LLMError, parse_json_strict

# Closed sets — must mirror prompts/recorder.md. Keep them in lockstep.
ALLOWED_TYPES: Final[frozenset[str]] = frozenset(
    {"milestone", "observation", "routine", "concern", "other"}
)
ALLOWED_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "language",
        "motor",
        "cognition",
        "social",
        "emotion",
        "self_care",
        "independence",
        "creativity",
        "music",
        "nature",
        "physical",
        "health",
        "routine",
        "other",
    }
)
ALLOWED_EMOTIONS: Final[frozenset[str]] = frozenset(
    {
        "happy",
        "proud",
        "excited",
        "curious",
        "calm",
        "affectionate",
        "sad",
        "angry",
        "frustrated",
        "scared",
        "tired",
        "anxious",
        "focused",
        "surprised",
        "confused",
    }
)

PROMPT_PATH: Final[Path] = Path(__file__).parent.parent / "prompts" / "recorder.md"


class RecorderError(RuntimeError):
    """Raised when the recorder cannot produce a valid event."""


@dataclass(frozen=True)
class StructuredEvent:
    id: str
    child_id: str
    timestamp: str  # ISO8601 with offset
    raw_text: str
    summary: str
    type: str
    domains: list[str]
    emotions: list[str] = field(default_factory=list)
    context: str = ""
    model_used: str = ""

    def as_row(self) -> dict[str, object]:
        """Map to the column shape of the events table."""
        import json as _json

        return {
            "id": self.id,
            "child_id": self.child_id,
            "timestamp": self.timestamp,
            "raw_text": self.raw_text,
            "summary": self.summary,
            "type": self.type,
            "domains_json": _json.dumps(self.domains, ensure_ascii=False),
            "emotions_json": _json.dumps(self.emotions, ensure_ascii=False),
            "context": self.context,
            "source": "manual",
            "model_used": self.model_used,
        }


def load_system_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise RecorderError(f"Recorder prompt not found at {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).astimezone().isoformat(timespec="seconds")


def _new_event_id(timestamp: str) -> str:
    # evt_2026_05_19_<6 hex> — readable + collision-resistant for single-user MVP
    date_part = re.sub(r"[^0-9]", "_", timestamp[:10])
    return f"evt_{date_part}_{secrets.token_hex(3)}"


def _coerce_str_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RecorderError(f"`{field_name}` must be a list, got {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise RecorderError(f"`{field_name}` items must be strings, got {item!r}")
        out.append(item)
    return out


def _validate_against(values: Sequence[str], allowed: frozenset[str], field_name: str) -> list[str]:
    invalid = [v for v in values if v not in allowed]
    if invalid:
        raise RecorderError(
            f"`{field_name}` contains values outside the closed set: {invalid!r}. "
            f"Allowed: {sorted(allowed)}"
        )
    # de-dupe, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def parse_recorder_output(
    raw: dict[str, object],
    *,
    child_id: str,
    raw_text: str,
    timestamp: str,
    model_used: str,
) -> StructuredEvent:
    """Validate the model's JSON dict → StructuredEvent. Raise on schema breach."""
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise RecorderError("`summary` is required and must be a non-empty string")

    type_ = raw.get("type")
    if not isinstance(type_, str) or type_ not in ALLOWED_TYPES:
        raise RecorderError(
            f"`type` must be one of {sorted(ALLOWED_TYPES)}, got {type_!r}"
        )

    domains = _validate_against(
        _coerce_str_list(raw.get("domains"), "domains"),
        ALLOWED_DOMAINS,
        "domains",
    )
    if not domains:
        raise RecorderError("`domains` must contain at least one value")
    if len(domains) > 3:
        domains = domains[:3]  # prompt asks for 1-3; trim defensively

    emotions = _validate_against(
        _coerce_str_list(raw.get("emotions"), "emotions"),
        ALLOWED_EMOTIONS,
        "emotions",
    )
    if len(emotions) > 3:
        emotions = emotions[:3]

    context_val = raw.get("context", "")
    if context_val is None:
        context = ""
    elif isinstance(context_val, str):
        context = context_val
    else:
        raise RecorderError("`context` must be a string if present")

    return StructuredEvent(
        id=_new_event_id(timestamp),
        child_id=child_id,
        timestamp=timestamp,
        raw_text=raw_text,
        summary=summary.strip(),
        type=type_,
        domains=domains,
        emotions=emotions,
        context=context,
        model_used=model_used,
    )


class Recorder:
    """Wrap an LLMClient with the recorder prompt + validation."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()
        self._system = load_system_prompt()

    def record(
        self,
        *,
        child_id: str,
        raw_text: str,
        timestamp: str | None = None,
    ) -> StructuredEvent:
        if not raw_text or not raw_text.strip():
            raise RecorderError("raw_text must not be empty")
        ts = timestamp or _now_iso()

        try:
            result = self._llm.generate(
                prompt=raw_text.strip(),
                system=self._system,
                purpose="recorder",
                json_mode=True,
            )
        except LLMError as e:
            raise RecorderError(str(e)) from e

        try:
            parsed = parse_json_strict(result.text)
        except LLMError as e:
            raise RecorderError(str(e)) from e

        return parse_recorder_output(
            parsed,
            child_id=child_id,
            raw_text=raw_text.strip(),
            timestamp=ts,
            model_used=result.model_used,
        )
