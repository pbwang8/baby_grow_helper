"""Phase 1 domain models.

Pydantic types used at the boundary (extractor → DB, API → DB). The plain
frozen-dataclass `StructuredEvent` from `src.agents.recorder` is left
unchanged — it predates Phase 1 and the recorder validates its own closed
sets; introducing Pydantic into the recorder hot path would ripple test
churn for no real gain.

Closed sets here mirror PRD `prd/phase1-signals.md` §2.1#1. They are also
re-exported as frozensets so the rule layer + tests can share them.
"""

from __future__ import annotations

import json
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---- closed sets (PRD §2.1#1) ----------------------------------------------

ALLOWED_SIGNAL_TYPES: Final[frozenset[str]] = frozenset(
    {
        "interest_pattern",
        "emotion_pattern",
        "skill_pattern",
        "anomaly",
        "growth_leap",
    }
)
ALLOWED_SIGNAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"active", "dormant", "dismissed"}
)


SignalType = Literal[
    "interest_pattern",
    "emotion_pattern",
    "skill_pattern",
    "anomaly",
    "growth_leap",
]
SignalStatus = Literal["active", "dormant", "dismissed"]


# ---- Signal model (PRD §2.1#1) ---------------------------------------------


class Signal(BaseModel):
    """A signal is an aggregated pattern across ≥ 2 events.

    Validation rules per PRD:
      - `evidence_event_ids` length must be ≥ 2 (single point isn't a signal)
      - `child_age_months` is frozen at signal birth (the caller is responsible
        for computing it from child.birthday at write time; the model only
        enforces ≥ 0)
      - `intensity`, `confidence` are clamped to [0.0, 1.0]
      - `delta_from_last_period` is nullable → distinguishes "no change" from
        "prior window too sparse" (PRD: 'in Phase 1, "no data" must not pretend
        to be "no change"')
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    child_id: str = Field(min_length=1, max_length=64)
    signal_type: SignalType
    domains: list[str] = Field(min_length=1, max_length=8)
    intensity: Annotated[float, Field(ge=0.0, le=1.0)]
    child_age_months: Annotated[int, Field(ge=0, le=600)]
    delta_from_last_period: Annotated[float, Field(ge=-1.0, le=1.0)] | None = None
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    first_seen_at: str = Field(min_length=1)  # ISO 8601 with offset
    last_seen_at: str = Field(min_length=1)
    evidence_event_ids: list[str] = Field(min_length=2)
    status: SignalStatus = "active"
    notes: str = ""

    @field_validator("evidence_event_ids")
    @classmethod
    def _evidence_unique_nonempty(cls, v: list[str]) -> list[str]:
        if any(not isinstance(x, str) or not x.strip() for x in v):
            raise ValueError("evidence_event_ids must be non-empty strings")
        if len(set(v)) != len(v):
            raise ValueError("evidence_event_ids must be unique")
        return v

    @field_validator("domains")
    @classmethod
    def _domains_nonempty_strings(cls, v: list[str]) -> list[str]:
        if any(not isinstance(x, str) or not x.strip() for x in v):
            raise ValueError("domains must be non-empty strings")
        # de-dupe preserving order
        seen: set[str] = set()
        out: list[str] = []
        for d in v:
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def as_row(self) -> dict[str, object]:
        """Map to the column shape of the signals table."""
        return {
            "id": self.id,
            "child_id": self.child_id,
            "signal_type": self.signal_type,
            "domains_json": json.dumps(self.domains, ensure_ascii=False),
            "intensity": self.intensity,
            "child_age_months": self.child_age_months,
            "delta_from_last_period": self.delta_from_last_period,
            "confidence": self.confidence,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "evidence_event_ids_json": json.dumps(
                self.evidence_event_ids, ensure_ascii=False
            ),
            "status": self.status,
            "notes": self.notes,
        }

    @classmethod
    def from_row(cls, row: dict[str, object]) -> Signal:
        """Inverse of as_row() for reading back from sqlite3.Row / dict."""
        domains_raw = row.get("domains_json", "[]")
        evidence_raw = row.get("evidence_event_ids_json", "[]")
        return cls(
            id=str(row["id"]),
            child_id=str(row["child_id"]),
            signal_type=str(row["signal_type"]),  # type: ignore[arg-type]
            domains=json.loads(str(domains_raw)),
            intensity=float(row["intensity"]),  # type: ignore[arg-type]
            child_age_months=int(row["child_age_months"]),  # type: ignore[call-overload]
            delta_from_last_period=(
                float(row["delta_from_last_period"])  # type: ignore[arg-type]
                if row.get("delta_from_last_period") is not None
                else None
            ),
            confidence=float(row["confidence"]),  # type: ignore[arg-type]
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
            evidence_event_ids=json.loads(str(evidence_raw)),
            status=str(row.get("status", "active")),  # type: ignore[arg-type]
            notes=str(row.get("notes", "")),
        )


# ---- helpers ---------------------------------------------------------------


def compute_age_months(birthday_iso: str, at_iso: str) -> int:
    """Return integer months between birthday (YYYY-MM-DD) and an ISO timestamp.

    Truncates toward zero. Used at signal-birth time to freeze the
    child_age_months field (PRD: signal must remember "5 months ago when child
    was 22 months old", not drift with the calendar).
    """
    import datetime as _dt

    bday = _dt.date.fromisoformat(birthday_iso)
    when = _dt.datetime.fromisoformat(at_iso).date()
    if when < bday:
        return 0
    months = (when.year - bday.year) * 12 + (when.month - bday.month)
    if when.day < bday.day:
        months -= 1
    return max(months, 0)


def new_signal_id(timestamp_iso: str, seq: int) -> str:
    """Deterministic id format: sig_YYYYMMDD_NNN. Caller picks `seq`."""
    import re as _re

    date_part = _re.sub(r"[^0-9]", "", timestamp_iso[:10])
    return f"sig_{date_part}_{seq:03d}"
