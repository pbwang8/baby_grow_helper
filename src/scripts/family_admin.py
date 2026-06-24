"""Phase 2.5 family invitation/admin helper.

This is not a public user-management system. It is an operator tool for the
invited-family trial:

- create up to N family access codes (default N=10)
- optionally create one owner/member row
- assign existing children to a family
- list families without printing raw access codes

Raw access codes are printed only at creation time and never stored.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from dataclasses import dataclass

from src.core import db as db_module
from src.core import family as family_module


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


def assign_child(*, child_id: str, family_id: str) -> None:
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


def list_families() -> list[dict[str, object]]:
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
