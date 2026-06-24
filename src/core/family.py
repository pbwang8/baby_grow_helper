"""Family access-code helpers for Phase 2.5.

This module is intentionally small and dependency-free. It gives the API a
single place to answer three questions:

1. Is family auth enabled for this process?
2. Which family does this access code identify?
3. Does this child belong to that family?

Raw family access codes are never stored. We hash them with SHA-256 and use
constant-time comparison for lookups.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import uuid
from typing import Final

FAMILY_CODE_HEADER: Final[str] = "X-Family-Code"
DEFAULT_TRIAL_FAMILY_CAP: Final[int] = 10

_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_HASH_PREFIX: Final[str] = "bgh-family-code-v1:"


def family_auth_required() -> bool:
    """Return whether API routes should require a family access code."""
    return os.environ.get("BGH_REQUIRE_FAMILY_AUTH", "").strip().lower() in _TRUE_VALUES


def trial_family_cap() -> int:
    """Maximum invited families for Phase 2.5.

    Defaults to 10 per PRD. Invalid env values deliberately fall back to the
    default instead of crashing an admin command.
    """
    raw = os.environ.get("BGH_FAMILY_TRIAL_MAX_FAMILIES", "").strip()
    if not raw:
        return DEFAULT_TRIAL_FAMILY_CAP
    try:
        cap = int(raw)
    except ValueError:
        return DEFAULT_TRIAL_FAMILY_CAP
    return max(1, cap)


def hash_access_code(code: str) -> str:
    """Hash a family access code for disk storage."""
    normalized = code.strip()
    if not normalized:
        raise ValueError("family access code must not be empty")
    digest = hashlib.sha256(f"{_HASH_PREFIX}{normalized}".encode()).hexdigest()
    return f"sha256:{digest}"


def verify_access_code(code: str, stored_hash: str) -> bool:
    """Constant-time check for a presented access code."""
    try:
        candidate = hash_access_code(code)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, stored_hash)


def ensure_family(
    conn: sqlite3.Connection,
    *,
    family_id: str = "fam_default",
    name: str = "Family",
    access_code: str,
) -> str:
    """Create or update a family access-code row. Returns `family_id`."""
    conn.execute(
        """
        INSERT INTO families(id, name, access_code_hash)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            access_code_hash = excluded.access_code_hash
        """,
        (family_id, name, hash_access_code(access_code)),
    )
    return family_id


def family_exists(conn: sqlite3.Connection, family_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM families WHERE id = ?", (family_id,)).fetchone()
    return row is not None


def count_families(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM families").fetchone()
    return int(row["n"])


def ensure_user(
    conn: sqlite3.Connection,
    *,
    user_id: str | None = None,
    display_name: str,
) -> str:
    """Create a minimal user row and return its id."""
    uid = user_id or uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO users(id, display_name)
        VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
        """,
        (uid, display_name),
    )
    return uid


def ensure_family_member(
    conn: sqlite3.Connection,
    *,
    family_id: str,
    user_id: str,
    role: str = "member",
    display_name: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO family_members(family_id, user_id, role, display_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(family_id, user_id) DO UPDATE SET
            role = excluded.role,
            display_name = excluded.display_name
        """,
        (family_id, user_id, role, display_name),
    )


def find_family_by_access_code(
    conn: sqlite3.Connection, access_code: str
) -> tuple[str, str] | None:
    """Return `(family_id, name)` for a valid access code."""
    rows = conn.execute("SELECT id, name, access_code_hash FROM families").fetchall()
    for row in rows:
        if verify_access_code(access_code, row["access_code_hash"]):
            return str(row["id"]), str(row["name"])
    return None


def assign_child_to_family(
    conn: sqlite3.Connection, *, child_id: str, family_id: str
) -> None:
    conn.execute(
        "UPDATE children SET family_id = ? WHERE id = ?",
        (family_id, child_id),
    )


def child_belongs_to_family(
    conn: sqlite3.Connection, *, child_id: str, family_id: str
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM children WHERE id = ? AND family_id = ?",
        (child_id, family_id),
    ).fetchone()
    return row is not None
