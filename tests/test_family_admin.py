"""Admin helper for the invited ≤10 family trial."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.core import db as db_module
from src.core import family as family_module
from src.scripts import family_admin
from src.scripts.family_admin import FamilyAdminError


def _seed_child(db_path: Path) -> None:
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO children(id, name, birthday) VALUES (?, ?, ?)",
            ("xiaoming", "小明", "2023-06-01"),
        )
    finally:
        conn.close()


def test_create_family_stores_hash_not_raw_code(tmp_db: Path) -> None:
    created = family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
        owner_name="Alpha Owner",
    )
    assert created.family_id == "fam_001"
    assert created.access_code == "alpha-secret"
    assert created.user_id == "user_fam_001_owner"

    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT access_code_hash FROM families WHERE id = ?", ("fam_001",)
        ).fetchone()
        member = conn.execute(
            """
            SELECT role, display_name
            FROM family_members
            WHERE family_id = ? AND user_id = ?
            """,
            ("fam_001", "user_fam_001_owner"),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row["access_code_hash"].startswith("sha256:")
    assert "alpha-secret" not in row["access_code_hash"]
    assert family_module.verify_access_code("alpha-secret", row["access_code_hash"])
    assert member["role"] == "owner"
    assert member["display_name"] == "Alpha Owner"


def test_create_family_enforces_trial_cap(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BGH_FAMILY_TRIAL_MAX_FAMILIES", "1")
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    with pytest.raises(FamilyAdminError, match="cap reached"):
        family_admin.create_family(
            family_id="fam_002",
            name="Beta Family",
            access_code="beta-secret",
        )


def test_create_family_can_update_existing_when_at_cap(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BGH_FAMILY_TRIAL_MAX_FAMILIES", "1")
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    updated = family_admin.create_family(
        family_id="fam_001",
        name="Alpha Renamed",
        access_code="new-secret",
    )
    assert updated.name == "Alpha Renamed"

    conn = db_module.get_conn(tmp_db)
    try:
        found = family_module.find_family_by_access_code(conn, "new-secret")
    finally:
        conn.close()
    assert found == ("fam_001", "Alpha Renamed")


def test_assign_child_to_family(tmp_db: Path) -> None:
    _seed_child(tmp_db)
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    family_admin.assign_child(child_id="xiaoming", family_id="fam_001")

    conn = db_module.get_conn(tmp_db)
    try:
        assert family_module.child_belongs_to_family(
            conn, child_id="xiaoming", family_id="fam_001"
        )
    finally:
        conn.close()


def test_assign_child_rejects_unknown_child(tmp_db: Path) -> None:
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    with pytest.raises(FamilyAdminError, match="child_id='ghost' not found"):
        family_admin.assign_child(child_id="ghost", family_id="fam_001")


def test_list_families_never_returns_access_code(tmp_db: Path) -> None:
    _seed_child(tmp_db)
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
        owner_name="Alpha Owner",
    )
    family_admin.assign_child(child_id="xiaoming", family_id="fam_001")

    rows = family_admin.list_families()
    assert rows == [
        {
            "family_id": "fam_001",
            "name": "Alpha Family",
            "member_count": 1,
            "child_count": 1,
            "created_at": rows[0]["created_at"],
        }
    ]
    assert "access_code" not in rows[0]
    assert "access_code_hash" not in rows[0]


def test_cli_create_prints_one_time_access_code(tmp_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = family_admin.main(
        [
            "create",
            "--family-id",
            "fam_001",
            "--name",
            "Alpha Family",
            "--access-code",
            "alpha-secret",
        ]
    )
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["family_id"] == "fam_001"
    assert body["access_code"] == "alpha-secret"
    assert body["warning"].startswith("Store this code securely")


def test_cli_list_prints_no_secrets(tmp_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    rc = family_admin.main(["list"])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["families"][0]["family_id"] == "fam_001"
    assert "access_code" not in body["families"][0]
    assert "alpha-secret" not in json.dumps(body, ensure_ascii=False)
