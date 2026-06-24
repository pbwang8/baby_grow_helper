"""Phase 2.5 family access-code foundation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.core import db as db_module
from src.core import family as family_module


def _seed_family_child(db_path: Path, *, code: str = "family-secret") -> None:
    conn = db_module.get_conn(db_path)
    try:
        family_module.ensure_family(
            conn,
            family_id="fam_test",
            name="Test Family",
            access_code=code,
        )
        family_module.ensure_family(
            conn,
            family_id="fam_other",
            name="Other Family",
            access_code="other-secret",
        )
        conn.execute(
            "INSERT INTO children(id, family_id, name, birthday) VALUES (?, ?, ?, ?)",
            ("xiaoming", "fam_test", "小明", "2023-06-01"),
        )
        conn.execute(
            """
            INSERT INTO children(id, family_id, name, birthday)
            VALUES (?, ?, ?, ?)
            """,
            ("other_child", "fam_other", "别人家孩子", "2023-06-01"),
        )
    finally:
        conn.close()


def test_hash_access_code_never_returns_raw_code() -> None:
    hashed = family_module.hash_access_code("family-secret")
    assert hashed.startswith("sha256:")
    assert "family-secret" not in hashed
    assert family_module.verify_access_code("family-secret", hashed)
    assert not family_module.verify_access_code("wrong", hashed)


def test_family_auth_endpoint_accepts_valid_code(tmp_db: Path) -> None:
    _seed_family_child(tmp_db)
    with TestClient(app) as client:
        r = client.post("/auth/family", json={"access_code": "family-secret"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "family_id": "fam_test",
        "family_name": "Test Family",
        "children": [
            {"id": "xiaoming", "name": "小明", "birthday": "2023-06-01"},
        ],
    }


def test_family_auth_endpoint_rejects_invalid_code(tmp_db: Path) -> None:
    _seed_family_child(tmp_db)
    with TestClient(app) as client:
        r = client.post("/auth/family", json={"access_code": "wrong"})
    assert r.status_code == 403


def test_events_require_family_code_when_enabled(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.get("/events", params={"child_id": "xiaoming"})
    assert r.status_code == 401


def test_events_reject_wrong_family_code(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.get(
            "/events",
            params={"child_id": "xiaoming"},
            headers={family_module.FAMILY_CODE_HEADER: "wrong"},
        )
    assert r.status_code == 403


def test_events_allow_valid_family_code(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.get(
            "/events",
            params={"child_id": "xiaoming"},
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 200
    assert r.json() == []


def test_children_lists_only_current_family(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.get(
            "/children",
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 200
    assert r.json() == [{"id": "xiaoming", "name": "小明", "birthday": "2023-06-01"}]


def test_family_code_cannot_read_other_child(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.get(
            "/events",
            params={"child_id": "other_child"},
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 404


def test_family_auth_disabled_keeps_local_dev_compatibility(tmp_db: Path) -> None:
    _seed_family_child(tmp_db)
    with TestClient(app) as client:
        r = client.get("/events", params={"child_id": "xiaoming"})
    assert r.status_code == 200
