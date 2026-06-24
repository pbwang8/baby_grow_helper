"""Admin helper for the invited ≤10 family trial."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
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


def test_create_child_in_family(tmp_db: Path) -> None:
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    family_admin.create_child(
        child_id="child_001",
        family_id="fam_001",
        name="小朋友",
        birthday="2023-06-01",
    )

    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT id, family_id, name, birthday FROM children WHERE id = ?",
            ("child_001",),
        ).fetchone()
    finally:
        conn.close()
    assert dict(row) == {
        "id": "child_001",
        "family_id": "fam_001",
        "name": "小朋友",
        "birthday": "2023-06-01",
    }


def test_create_child_rejects_unknown_family(tmp_db: Path) -> None:
    with pytest.raises(FamilyAdminError, match="family_id='ghost' not found"):
        family_admin.create_child(
            child_id="child_001",
            family_id="ghost",
            name="小朋友",
            birthday="2023-06-01",
        )


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


def test_cli_create_child(tmp_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
    )
    rc = family_admin.main(
        [
            "create-child",
            "--child-id",
            "child_001",
            "--family-id",
            "fam_001",
            "--name",
            "小朋友",
            "--birthday",
            "2023-06-01",
        ]
    )
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body == {
        "child_id": "child_001",
        "family_id": "fam_001",
        "name": "小朋友",
        "birthday": "2023-06-01",
    }


def test_postgres_create_list_and_assign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePgConnection()
    monkeypatch.setenv("BGH_RUNTIME_DB_BACKEND", "postgres")
    monkeypatch.setattr(family_admin, "_connect_postgres", lambda: fake)

    created = family_admin.create_family(
        family_id="fam_001",
        name="Alpha Family",
        access_code="alpha-secret",
        owner_name="Alpha Owner",
    )
    fake.children["xiaoming"] = {"id": "xiaoming", "family_id": None}
    family_admin.assign_child(child_id="xiaoming", family_id="fam_001")
    family_admin.create_child(
        child_id="child_001",
        family_id="fam_001",
        name="小朋友",
        birthday="2023-06-01",
    )
    rows = family_admin.list_families()

    assert created.user_id == "user_fam_001_owner"
    assert fake.committed_count == 3
    assert fake.children["xiaoming"]["family_id"] == "fam_001"
    assert fake.children["child_001"]["name"] == "小朋友"
    assert rows == [
        {
            "family_id": "fam_001",
            "name": "Alpha Family",
            "member_count": 1,
            "child_count": 2,
            "created_at": "2026-06-24T00:00:00Z",
        }
    ]
    assert "alpha-secret" not in json.dumps(rows, ensure_ascii=False)


class _FakePgConnection(AbstractContextManager["_FakePgConnection"]):
    def __init__(self) -> None:
        self.committed_count = 0
        self.families: dict[str, dict[str, object]] = {}
        self.users: dict[str, dict[str, object]] = {}
        self.members: set[tuple[str, str]] = set()
        self.children: dict[str, dict[str, object]] = {}

    def __exit__(self, *exc: object) -> None:
        return None

    def cursor(self) -> _FakePgCursor:
        return _FakePgCursor(self)

    def commit(self) -> None:
        self.committed_count += 1


class _FakePgCursor(AbstractContextManager["_FakePgCursor"]):
    def __init__(self, conn: _FakePgConnection) -> None:
        self.conn = conn
        self._one: object | None = None
        self._many: list[dict[str, object]] = []

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        compact = " ".join(sql.split())
        if compact.startswith("SELECT 1 FROM families"):
            family_id = str(params[0])
            self._one = 1 if family_id in self.conn.families else None
            return
        if compact.startswith("SELECT COUNT(*) AS n FROM families"):
            self._one = {"n": len(self.conn.families)}
            return
        if compact.startswith("INSERT INTO families"):
            family_id_obj, name_obj, access_hash_obj = params
            self.conn.families[str(family_id_obj)] = {
                "id": family_id_obj,
                "name": name_obj,
                "access_code_hash": access_hash_obj,
                "created_at": "2026-06-24T00:00:00Z",
            }
            return
        if compact.startswith("INSERT INTO users"):
            user_id, display_name = params
            self.conn.users[str(user_id)] = {
                "id": user_id,
                "display_name": display_name,
            }
            return
        if compact.startswith("INSERT INTO family_members"):
            member_family_id, member_user_id, _role, _display_name = params
            self.conn.members.add((str(member_family_id), str(member_user_id)))
            return
        if compact.startswith("SELECT id FROM children"):
            child_id = str(params[0])
            self._one = self.conn.children.get(child_id)
            return
        if compact.startswith("SELECT id FROM families"):
            family_id = str(params[0])
            self._one = self.conn.families.get(family_id)
            return
        if compact.startswith("UPDATE children SET family_id"):
            new_family_id, update_child_id = params
            self.conn.children[str(update_child_id)]["family_id"] = new_family_id
            return
        if compact.startswith("INSERT INTO children"):
            child_id_obj, family_id_obj, name_obj, birthday_obj = params
            self.conn.children[str(child_id_obj)] = {
                "id": child_id_obj,
                "family_id": family_id_obj,
                "name": name_obj,
                "birthday": birthday_obj,
            }
            return
        if "FROM families f LEFT JOIN family_members" in compact:
            self._many = [
                {
                    "id": family["id"],
                    "name": family["name"],
                    "created_at": family["created_at"],
                    "member_count": sum(
                        1 for fam_id, _user_id in self.conn.members if fam_id == family_id
                    ),
                    "child_count": sum(
                        1
                        for child in self.conn.children.values()
                        if child["family_id"] == family_id
                    ),
                }
                for family_id, family in self.conn.families.items()
            ]

    def fetchone(self) -> object | None:
        return self._one

    def fetchall(self) -> list[dict[str, object]]:
        return self._many
