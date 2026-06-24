"""Phase 2.5 family invitation/admin helper.

This is not a public user-management system. It is an operator tool for the
invited-family trial:

- create up to N family access codes (default N=10)
- create/update child rows inside a family
- optionally create one owner/member row
- assign existing children to a family
- list families without printing raw access codes

Raw access codes are printed only at creation time and never stored.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import secrets
import sys
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, cast

from src.core import db as db_module
from src.core import family as family_module
from src.core import runtime_store


class FamilyAdminError(RuntimeError):
    """Admin command cannot be completed safely."""


@dataclass(frozen=True)
class CreatedFamily:
    family_id: str
    name: str
    access_code: str
    user_id: str | None
    cap: int


def _new_access_code() -> str:
    # 16 URL-safe chars gives enough entropy for a private family trial while
    # staying human-copyable in chat.
    return secrets.token_urlsafe(12)


def create_family(
    *,
    family_id: str,
    name: str,
    access_code: str | None = None,
    owner_name: str | None = None,
    user_id: str | None = None,
) -> CreatedFamily:
    """Create/update one family under the Phase 2.5 invited-trial cap."""
    if runtime_store.runtime_backend() == "postgres":
        return _create_family_postgres(
            family_id=family_id,
            name=name,
            access_code=access_code,
            owner_name=owner_name,
            user_id=user_id,
        )
    return _create_family_sqlite(
        family_id=family_id,
        name=name,
        access_code=access_code,
        owner_name=owner_name,
        user_id=user_id,
    )


def _create_family_sqlite(
    *,
    family_id: str,
    name: str,
    access_code: str | None = None,
    owner_name: str | None = None,
    user_id: str | None = None,
) -> CreatedFamily:
    code = access_code or _new_access_code()
    cap = family_module.trial_family_cap()
    conn = db_module.get_conn()
    try:
        exists = family_module.family_exists(conn, family_id)
        if not exists and family_module.count_families(conn) >= cap:
            raise FamilyAdminError(
                f"family trial cap reached ({cap}); do not exceed the invited cohort"
            )

        with db_module.transactional(conn):
            family_module.ensure_family(
                conn,
                family_id=family_id,
                name=name,
                access_code=code,
            )
            uid: str | None = None
            if owner_name:
                uid = family_module.ensure_user(
                    conn,
                    user_id=user_id or f"user_{family_id}_owner",
                    display_name=owner_name,
                )
                family_module.ensure_family_member(
                    conn,
                    family_id=family_id,
                    user_id=uid,
                    role="owner",
                    display_name=owner_name,
                )
        return CreatedFamily(
            family_id=family_id,
            name=name,
            access_code=code,
            user_id=uid,
            cap=cap,
        )
    finally:
        conn.close()


def _create_family_postgres(
    *,
    family_id: str,
    name: str,
    access_code: str | None = None,
    owner_name: str | None = None,
    user_id: str | None = None,
) -> CreatedFamily:
    code = access_code or _new_access_code()
    cap = family_module.trial_family_cap()
    uid: str | None = None
    with _connect_postgres() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM families WHERE id = %s", (family_id,))
        exists = cur.fetchone() is not None
        cur.execute("SELECT COUNT(*) AS n FROM families")
        count_row = _row_to_dict(cur.fetchone())
        if not exists and _int_field(count_row, "n") >= cap:
            raise FamilyAdminError(
                f"family trial cap reached ({cap}); do not exceed the invited cohort"
            )

        cur.execute(
            """
            INSERT INTO families(id, name, access_code_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                access_code_hash = excluded.access_code_hash
            """,
            (family_id, name, family_module.hash_access_code(code)),
        )
        if owner_name:
            uid = user_id or f"user_{family_id}_owner"
            cur.execute(
                """
                INSERT INTO users(id, display_name)
                VALUES (%s, %s)
                ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
                """,
                (uid, owner_name),
            )
            cur.execute(
                """
                INSERT INTO family_members(family_id, user_id, role, display_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(family_id, user_id) DO UPDATE SET
                    role = excluded.role,
                    display_name = excluded.display_name
                """,
                (family_id, uid, "owner", owner_name),
            )
        conn.commit()
    return CreatedFamily(
        family_id=family_id,
        name=name,
        access_code=code,
        user_id=uid,
        cap=cap,
    )


def assign_child(*, child_id: str, family_id: str) -> None:
    if runtime_store.runtime_backend() == "postgres":
        _assign_child_postgres(child_id=child_id, family_id=family_id)
        return
    _assign_child_sqlite(child_id=child_id, family_id=family_id)


def _assign_child_sqlite(*, child_id: str, family_id: str) -> None:
    conn = db_module.get_conn()
    try:
        child = conn.execute("SELECT id FROM children WHERE id = ?", (child_id,)).fetchone()
        if child is None:
            raise FamilyAdminError(f"child_id={child_id!r} not found")
        if not family_module.family_exists(conn, family_id):
            raise FamilyAdminError(f"family_id={family_id!r} not found")
        with db_module.transactional(conn):
            family_module.assign_child_to_family(
                conn, child_id=child_id, family_id=family_id
            )
    finally:
        conn.close()


def _assign_child_postgres(*, child_id: str, family_id: str) -> None:
    with _connect_postgres() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM children WHERE id = %s", (child_id,))
        if cur.fetchone() is None:
            raise FamilyAdminError(f"child_id={child_id!r} not found")
        cur.execute("SELECT id FROM families WHERE id = %s", (family_id,))
        if cur.fetchone() is None:
            raise FamilyAdminError(f"family_id={family_id!r} not found")
        cur.execute(
            "UPDATE children SET family_id = %s WHERE id = %s",
            (family_id, child_id),
        )
        conn.commit()


def create_child(
    *,
    child_id: str,
    family_id: str,
    name: str,
    birthday: str,
) -> None:
    """Create/update one child row inside a family."""
    if runtime_store.runtime_backend() == "postgres":
        _create_child_postgres(
            child_id=child_id,
            family_id=family_id,
            name=name,
            birthday=birthday,
        )
        return
    _create_child_sqlite(
        child_id=child_id,
        family_id=family_id,
        name=name,
        birthday=birthday,
    )


def _create_child_sqlite(
    *,
    child_id: str,
    family_id: str,
    name: str,
    birthday: str,
) -> None:
    conn = db_module.get_conn()
    try:
        if not family_module.family_exists(conn, family_id):
            raise FamilyAdminError(f"family_id={family_id!r} not found")
        with db_module.transactional(conn):
            conn.execute(
                """
                INSERT INTO children(id, family_id, name, birthday)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    family_id = excluded.family_id,
                    name = excluded.name,
                    birthday = excluded.birthday
                """,
                (child_id, family_id, name, birthday),
            )
    finally:
        conn.close()


def _create_child_postgres(
    *,
    child_id: str,
    family_id: str,
    name: str,
    birthday: str,
) -> None:
    with _connect_postgres() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM families WHERE id = %s", (family_id,))
        if cur.fetchone() is None:
            raise FamilyAdminError(f"family_id={family_id!r} not found")
        cur.execute(
            """
            INSERT INTO children(id, family_id, name, birthday)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                family_id = excluded.family_id,
                name = excluded.name,
                birthday = excluded.birthday
            """,
            (child_id, family_id, name, birthday),
        )
        conn.commit()


def list_families() -> list[dict[str, object]]:
    if runtime_store.runtime_backend() == "postgres":
        return _list_families_postgres()
    return _list_families_sqlite()


def _list_families_sqlite() -> list[dict[str, object]]:
    conn = db_module.get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                f.id,
                f.name,
                f.created_at,
                COUNT(DISTINCT fm.user_id) AS member_count,
                COUNT(DISTINCT c.id) AS child_count
            FROM families f
            LEFT JOIN family_members fm ON fm.family_id = f.id
            LEFT JOIN children c ON c.family_id = f.id
            GROUP BY f.id, f.name, f.created_at
            ORDER BY f.created_at ASC, f.id ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "family_id": row["id"],
            "name": row["name"],
            "member_count": int(row["member_count"]),
            "child_count": int(row["child_count"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _list_families_postgres() -> list[dict[str, object]]:
    with _connect_postgres() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                f.id,
                f.name,
                f.created_at,
                COUNT(DISTINCT fm.user_id) AS member_count,
                COUNT(DISTINCT c.id) AS child_count
            FROM families f
            LEFT JOIN family_members fm ON fm.family_id = f.id
            LEFT JOIN children c ON c.family_id = f.id
            GROUP BY f.id, f.name, f.created_at
            ORDER BY f.created_at ASC, f.id ASC
            """
        )
        rows = [_row_to_dict(row) for row in cur.fetchall()]
    return [
        {
            "family_id": str(row["id"]),
            "name": str(row["name"]),
            "member_count": _int_field(row, "member_count"),
            "child_count": _int_field(row, "child_count"),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def _connect_postgres() -> AbstractContextManager[Any]:
    try:
        psycopg: Any = importlib.import_module("psycopg")
        rows: Any = importlib.import_module("psycopg.rows")
    except ImportError as e:  # pragma: no cover - optional deploy dependency
        raise FamilyAdminError(
            "Postgres family admin requires psycopg in the runtime image"
        ) from e
    database_url = os.environ.get("BGH_DATABASE_URL", "")
    if not database_url:
        raise FamilyAdminError("BGH_DATABASE_URL is required for Postgres family admin")
    return cast(
        AbstractContextManager[Any],
        psycopg.connect(database_url, row_factory=rows.dict_row),
    )


def _row_to_dict(row: Mapping[str, object] | object | None) -> dict[str, object]:
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        row_any: Any = row
        return {str(key): row_any[key] for key in keys()}
    raise FamilyAdminError(f"Unsupported database row shape: {type(row).__name__}")


def _int_field(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise FamilyAdminError(f"{key} must be int-compatible, got {type(value).__name__}")


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BabyGrowHelper family admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create/update a family invite")
    p_create.add_argument("--family-id", required=True)
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--access-code", help="Optional explicit code")
    p_create.add_argument("--owner-name", help="Optional owner display name")
    p_create.add_argument("--user-id", help="Optional owner user id")

    p_assign = sub.add_parser("assign-child", help="Assign an existing child to a family")
    p_assign.add_argument("--child-id", required=True)
    p_assign.add_argument("--family-id", required=True)

    p_child = sub.add_parser("create-child", help="Create/update a child in a family")
    p_child.add_argument("--child-id", required=True)
    p_child.add_argument("--family-id", required=True)
    p_child.add_argument("--name", required=True)
    p_child.add_argument("--birthday", required=True, help="YYYY-MM-DD")

    sub.add_parser("list", help="List families without access-code secrets")

    args = parser.parse_args(argv)

    if args.cmd == "create":
        created = create_family(
            family_id=args.family_id,
            name=args.name,
            access_code=args.access_code,
            owner_name=args.owner_name,
            user_id=args.user_id,
        )
        _print_json(
            {
                "family_id": created.family_id,
                "name": created.name,
                "access_code": created.access_code,
                "user_id": created.user_id,
                "trial_family_cap": created.cap,
                "warning": "Store this code securely; only its hash is persisted.",
            }
        )
        return 0

    if args.cmd == "assign-child":
        assign_child(child_id=args.child_id, family_id=args.family_id)
        _print_json({"child_id": args.child_id, "family_id": args.family_id})
        return 0

    if args.cmd == "create-child":
        create_child(
            child_id=args.child_id,
            family_id=args.family_id,
            name=args.name,
            birthday=args.birthday,
        )
        _print_json(
            {
                "child_id": args.child_id,
                "family_id": args.family_id,
                "name": args.name,
                "birthday": args.birthday,
            }
        )
        return 0

    if args.cmd == "list":
        _print_json({"families": list_families()})
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except FamilyAdminError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2) from e
