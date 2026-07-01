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


def test_create_child_scoped_to_current_family(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.post(
            "/children",
            json={"name": "小朋友", "birthday": "2024-01-02"},
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("child_")
    assert body["name"] == "小朋友"

    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT family_id, name, birthday FROM children WHERE id = ?",
            (body["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["family_id"] == "fam_test"
    assert row["name"] == "小朋友"
    assert row["birthday"] == "2024-01-02"


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


def test_heatmap_uses_family_scoped_runtime_store(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    conn = db_module.get_conn(tmp_db)
    try:
        conn.execute(
            """
            INSERT INTO events
              (id, child_id, timestamp, raw_text, summary, type,
               domains_json, emotions_json, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_heatmap_family",
                "xiaoming",
                "2026-05-19T10:00:00+08:00",
                "今天小明搭积木",
                "小明搭积木",
                "observation",
                '["creativity", "motor"]',
                "[]",
                "manual",
            ),
        )
        conn.execute(
            """
            INSERT INTO events
              (id, child_id, timestamp, raw_text, summary, type,
               domains_json, emotions_json, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_other_family",
                "other_child",
                "2026-05-19T10:00:00+08:00",
                "别人家孩子唱歌",
                "别人家孩子唱歌",
                "observation",
                '["music"]',
                "[]",
                "manual",
            ),
        )
    finally:
        conn.close()

    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.get(
            "/heatmap",
            params={"child_id": "xiaoming"},
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert {cell["domain"] for cell in body} == {"creativity", "motor"}
    assert all(cell["age_months"] == 35 for cell in body)


def test_family_auth_disabled_keeps_local_dev_compatibility(tmp_db: Path) -> None:
    _seed_family_child(tmp_db)
    with TestClient(app) as client:
        r = client.get("/events", params={"child_id": "xiaoming"})
    assert r.status_code == 200


def test_trial_feedback_scoped_to_current_family(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.post(
            "/feedback",
            json={
                "child_id": "xiaoming",
                "page": "/timeline",
                "category": "confusing",
                "message": "时间轴没有看到刚记录的内容",
                "contact": "tester",
            },
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["category"] == "confusing"
    assert body["page"] == "/timeline"

    conn = db_module.get_conn(tmp_db)
    try:
        row = conn.execute(
            "SELECT family_id, child_id, message FROM trial_feedback WHERE id = ?",
            (body["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["family_id"] == "fam_test"
    assert row["child_id"] == "xiaoming"
    assert row["message"] == "时间轴没有看到刚记录的内容"


def test_trial_feedback_rejects_other_family_child(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_family_child(tmp_db)
    monkeypatch.setenv("BGH_REQUIRE_FAMILY_AUTH", "1")
    with TestClient(app) as client:
        r = client.post(
            "/feedback",
            json={
                "child_id": "other_child",
                "page": "/timeline",
                "category": "bug",
                "message": "看到了别人家的孩子",
            },
            headers={family_module.FAMILY_CODE_HEADER: "family-secret"},
        )
    assert r.status_code == 404
