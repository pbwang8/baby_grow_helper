"""Phase 1 M1.4 — backfill historical events from a JSONL file.

Why this exists:
  Real users come to the product with a backlog of memories — "我记得他
  18 个月就会自己穿鞋了" — that they want in the system. Asking them to
  re-type each one through the recorder is a non-starter. This script
  takes a JSONL where each line IS the structured event already, and
  inserts directly to the `events` table.

Bypass rationale:
  - Backfill data is **memory-precise but speech-imprecise**: parents
    type compact facts ("19mo: 自己穿鞋"), not narrative observations.
    The recorder Agent's job is to interpret narrative, so it would
    mostly get in the way. We trust the JSONL author.
  - Each row already has `timestamp`, so `id` is deterministic from it.
  - The `summary` field is whatever the JSONL says; downstream rule
    layer + LLM judge work on `domains`/`type`/`emotions` regardless.

Two flags:
  --child            target child_id (must already exist)
  --file             path to JSONL
  --re-extract-signals
                     after loading, run signal extractor against the
                     enlarged event window. Off by default — extraction
                     hits the local LLM and you may want to do it later.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import secrets
import sqlite3
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agents.recorder import (
    ALLOWED_DOMAINS,
    ALLOWED_EMOTIONS,
    ALLOWED_TYPES,
)
from src.core import db as db_module

logger = logging.getLogger(__name__)


class BackfillError(RuntimeError):
    """Raised when a fixture row is malformed."""


@dataclass(frozen=True)
class BackfillRecord:
    """A row from the backfill JSONL.

    Mirrors `events` table columns but accepts simpler input — `domains`
    and `emotions` are JSON arrays in the file but plain `list[str]`
    here.
    """

    timestamp: str
    summary: str
    type: str
    domains: list[str]
    emotions: list[str]
    context: str
    raw_text: str = ""


# ---- parsing ---------------------------------------------------------------


def parse_jsonl(path: Path) -> list[BackfillRecord]:
    """Read + validate a backfill JSONL file. Empty lines tolerated."""
    if not path.exists():
        raise BackfillError(f"file not found: {path}")
    out: list[BackfillRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise BackfillError(f"line {lineno}: invalid JSON ({e})") from e
            if not isinstance(raw, dict):
                raise BackfillError(f"line {lineno}: expected object, got {type(raw).__name__}")
            out.append(_validate(raw, lineno))
    return out


def _validate(raw: dict[str, Any], lineno: int) -> BackfillRecord:
    """Closed-set validation against the recorder's vocabulary.

    We reuse `ALLOWED_*` constants instead of re-declaring them, so a
    drift in one place fails the build everywhere.
    """
    required = ("timestamp", "summary", "type", "domains")
    for key in required:
        if key not in raw:
            raise BackfillError(f"line {lineno}: missing field {key!r}")

    type_ = raw["type"]
    if type_ not in ALLOWED_TYPES:
        raise BackfillError(
            f"line {lineno}: type={type_!r} not in {sorted(ALLOWED_TYPES)}"
        )

    domains = raw.get("domains") or []
    if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
        raise BackfillError(f"line {lineno}: domains must be list[str]")
    if not domains:
        raise BackfillError(f"line {lineno}: domains must be non-empty")
    bad = [d for d in domains if d not in ALLOWED_DOMAINS]
    if bad:
        raise BackfillError(f"line {lineno}: unknown domains {bad!r}")

    emotions = raw.get("emotions") or []
    if not isinstance(emotions, list) or not all(isinstance(e, str) for e in emotions):
        raise BackfillError(f"line {lineno}: emotions must be list[str]")
    bad_e = [e for e in emotions if e not in ALLOWED_EMOTIONS]
    if bad_e:
        raise BackfillError(f"line {lineno}: unknown emotions {bad_e!r}")

    timestamp = str(raw["timestamp"])
    try:
        dt.datetime.fromisoformat(timestamp)
    except ValueError as e:
        raise BackfillError(f"line {lineno}: bad timestamp {timestamp!r}") from e

    return BackfillRecord(
        timestamp=timestamp,
        summary=str(raw["summary"]),
        type=type_,
        domains=list(domains),
        emotions=list(emotions),
        context=str(raw.get("context", "")),
        raw_text=str(raw.get("raw_text", raw["summary"])),
    )


# ---- insertion ------------------------------------------------------------


def insert_records(
    conn: sqlite3.Connection,
    child_id: str,
    records: Iterable[BackfillRecord],
) -> list[str]:
    """Insert all records under one transaction. Returns generated event ids."""
    child = conn.execute("SELECT id FROM children WHERE id = ?", (child_id,)).fetchone()
    if child is None:
        raise BackfillError(f"child_id={child_id!r} not found — run `make seed` first")

    ids: list[str] = []
    with db_module.transactional(conn):
        for rec in records:
            eid = _backfill_event_id(rec.timestamp)
            conn.execute(
                """
                INSERT INTO events
                  (id, child_id, timestamp, raw_text, summary, type,
                   domains_json, emotions_json, context, source, model_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'backfill', NULL)
                """,
                (
                    eid,
                    child_id,
                    rec.timestamp,
                    rec.raw_text,
                    rec.summary,
                    rec.type,
                    json.dumps(rec.domains, ensure_ascii=False),
                    json.dumps(rec.emotions, ensure_ascii=False),
                    rec.context,
                ),
            )
            ids.append(eid)
    return ids


def _backfill_event_id(timestamp: str) -> str:
    """Same shape as recorder._new_event_id but tagged with `bf` for grep-ability.

    Identifying backfilled rows by id is occasionally useful when
    diagnosing "why did this fixture not match my real data".
    """
    import re

    date_part = re.sub(r"[^0-9]", "_", timestamp[:10])
    return f"evt_bf_{date_part}_{secrets.token_hex(3)}"


# ---- CLI ------------------------------------------------------------------


def _iter_chunks(seq: list[BackfillRecord], n: int) -> Iterator[list[BackfillRecord]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="BabyGrowHelper backfill loader")
    parser.add_argument("--child", required=True, help="child_id (must exist)")
    parser.add_argument("--file", required=True, type=Path, help="JSONL fixture path")
    parser.add_argument(
        "--re-extract-signals",
        action="store_true",
        help="After insert, run SignalExtractor against the new window.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=14,
        help="Window for signal extraction (default 14, per PRD).",
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="ISO timestamp to use as 'now' for signal extraction (testability).",
    )
    args = parser.parse_args(argv)

    records = parse_jsonl(args.file)
    logger.info("parsed %d records from %s", len(records), args.file)
    if not records:
        logger.warning("nothing to insert")
        return 0

    conn = db_module.get_conn()
    try:
        ids = insert_records(conn, args.child, records)
    finally:
        conn.close()
    logger.info("inserted %d events under child=%s", len(ids), args.child)

    if args.re_extract_signals:
        # local import: SignalExtractor pulls in the LLM client, and we
        # don't want that in the import path of `make backfill --help`.
        from src.agents.signal_extractor import SignalExtractor

        extractor = SignalExtractor()
        signals = extractor.extract_for_child(
            child_id=args.child,
            window_days=args.window_days,
            now_iso=args.now,
        )
        logger.info("extracted %d signals", len(signals))

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
