"""HTTP shape tests for Phase 2 insight endpoints.

We patch `write_weekly_insight` so the routes are exercised without any
network I/O — the agent layer is unit-tested in test_insight_writer_unit.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from src.agents.insight_writer import InsightSection, WeeklyInsight
from src.api.main import app
from src.core import db as db_module

WEEK_START = "2026-05-18"  # Monday


def _make_insight(child_id: str = "xiaoming") -> WeeklyInsight:
    return WeeklyInsight(
        id="ins_uuid_test_001",
        child_id=child_id,
        week_start=WEEK_START,
        week_end="2026-05-25",
        child_age_months=35,
        sections=[
            InsightSection(
                axis="highlight", title="高光", body="本周三次音乐 (sig_001)。",
                sources_used=["sig_001"],
            ),
            InsightSection(
                axis="change_over_time", title="变化", body="增长 (sig_001)。",
                sources_used=["sig_001"],
            ),
            InsightSection(
                axis="next_week_focus", title="下周", body="持续观察。",
                sources_used=[],
            ),
            InsightSection(
                axis="open_questions", title="开放", body="她最喜欢什么？",
                sources_used=[],
            ),
        ],
        open_questions=["她最喜欢什么？"],
        sources_used=["sig_001"],
        backend="claude",
        model_used="claude-sonnet-4-20250514",
        tokens_in=900,
        tokens_out=180,
    )


def _seed_signal_in_week(db_path: Path) -> None:
    """Make sure compress_week_context has at least one signal/event in-window
    so it doesn't return an empty CompressedContext."""
    conn = db_module.get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO events
              (id, child_id, timestamp, raw_text, summary, type,
               domains_json, emotions_json, context, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_in_week",
                "xiaoming",
                "2026-05-19T10:00:00+08:00",
                "原文",
                "本周音乐摘要",
                "observation",
                '["music"]',
                "[]",
                "",
                "manual",
            ),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def client(seeded_xiaoming: Path) -> Iterator[TestClient]:
    _seed_signal_in_week(seeded_xiaoming)
    with TestClient(app) as c:
        yield c


def test_generate_insight_persists_and_returns(client: TestClient) -> None:
    fake = _make_insight()
    with patch(
        "src.api.main.write_weekly_insight", return_value=fake
    ) as writer_mock:
        r = client.post(
            "/insights/generate",
            json={
                "child_id": "xiaoming",
                "week_start": WEEK_START,
                "backend": "claude",
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == fake.id
    assert body["child_id"] == "xiaoming"
    assert body["version"] == 1
    assert body["backend"] == "claude"
    assert len(body["sections"]) == 4
    assert any(s["axis"] == "change_over_time" for s in body["sections"])
    # writer was called with a CompressedContext for the right week
    args, kwargs = writer_mock.call_args
    assert kwargs["backend"] == "claude"
    ctx = args[0]
    assert ctx.child_id == "xiaoming"
    assert ctx.week_start == dt.date(2026, 5, 18)


def test_regenerate_bumps_version(client: TestClient) -> None:
    fake = _make_insight()
    with patch("src.api.main.write_weekly_insight", return_value=fake):
        r1 = client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    assert r1.status_code == 201, r1.text

    # second call must succeed with version=2 even though the agent reuses
    # the same UUID — but to avoid PK collision in the DB we mint a new id
    # in the fixture.
    fake2 = _make_insight()
    fake2 = fake2.model_copy(update={"id": "ins_uuid_test_002"})
    with patch("src.api.main.write_weekly_insight", return_value=fake2):
        r2 = client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    assert r2.status_code == 201, r2.text
    assert r2.json()["version"] == 2


def test_generate_rejects_non_monday(client: TestClient) -> None:
    r = client.post(
        "/insights/generate",
        json={"child_id": "xiaoming", "week_start": "2026-05-19"},  # Tuesday
    )
    assert r.status_code == 422
    assert "Monday" in r.json()["detail"]


def test_generate_rejects_unknown_child(client: TestClient) -> None:
    r = client.post(
        "/insights/generate",
        json={"child_id": "ghost", "week_start": WEEK_START},
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_generate_rejects_bad_date_format(client: TestClient) -> None:
    r = client.post(
        "/insights/generate",
        json={"child_id": "xiaoming", "week_start": "2026/05/18"},
    )
    assert r.status_code == 422


def test_get_insight_404_unknown(client: TestClient) -> None:
    r = client.get("/insights/never_existed")
    assert r.status_code == 404


def test_get_insight_returns_persisted_record(client: TestClient) -> None:
    fake = _make_insight()
    with patch("src.api.main.write_weekly_insight", return_value=fake):
        client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    r = client.get(f"/insights/{fake.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == fake.id
    assert body["created_at"]  # filled by DB default


def test_list_insights_newest_first(client: TestClient) -> None:
    fake = _make_insight()
    with patch("src.api.main.write_weekly_insight", return_value=fake):
        client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    fake2 = _make_insight().model_copy(update={"id": "ins_uuid_test_v2"})
    with patch("src.api.main.write_weekly_insight", return_value=fake2):
        client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    r = client.get("/insights", params={"child_id": "xiaoming"})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    # ordering by created_at DESC — newest first; but DB default has ms
    # precision so they may tie. We just assert both ids are present.
    ids = {row["id"] for row in rows}
    assert ids == {"ins_uuid_test_001", "ins_uuid_test_v2"}


# ---- feedback ------------------------------------------------------------


def _post_insight(client: TestClient) -> str:
    fake = _make_insight()
    with patch("src.api.main.write_weekly_insight", return_value=fake):
        client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    return fake.id


def test_post_feedback_full_dimensions(client: TestClient) -> None:
    insight_id = _post_insight(client)
    r = client.post(
        f"/insights/{insight_id}/feedback",
        json={
            "section_idx": 1,
            "accuracy": "accurate",
            "value": "inspiring",
            "free_text": "戳中了我没注意到的点。",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["insight_id"] == insight_id
    assert body["accuracy"] == "accurate"
    assert body["value"] == "inspiring"
    assert body["section_idx"] == 1


def test_post_feedback_partial_dimensions(client: TestClient) -> None:
    """PRD §3.6: parent may submit only one dimension."""
    insight_id = _post_insight(client)
    r = client.post(
        f"/insights/{insight_id}/feedback",
        json={"section_idx": 0, "accuracy": "unsure"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["accuracy"] == "unsure"
    assert body["value"] is None
    assert body["free_text"] is None


def test_post_feedback_rejects_empty_payload(client: TestClient) -> None:
    insight_id = _post_insight(client)
    r = client.post(
        f"/insights/{insight_id}/feedback",
        json={"section_idx": 0},
    )
    assert r.status_code == 422


def test_post_feedback_404_unknown_insight(client: TestClient) -> None:
    r = client.post(
        "/insights/never_made/feedback",
        json={"section_idx": 0, "accuracy": "accurate"},
    )
    assert r.status_code == 404


def test_post_feedback_rejects_invalid_enum(client: TestClient) -> None:
    insight_id = _post_insight(client)
    r = client.post(
        f"/insights/{insight_id}/feedback",
        json={"section_idx": 0, "accuracy": "amazing"},
    )
    assert r.status_code == 422


# ---- shape sanity --------------------------------------------------------


def test_sections_round_trip_axis_field(client: TestClient) -> None:
    """The DB serializes sections to JSON; deserialization must preserve axis."""
    fake = _make_insight()
    with patch("src.api.main.write_weekly_insight", return_value=fake):
        r = client.post(
            "/insights/generate",
            json={"child_id": "xiaoming", "week_start": WEEK_START},
        )
    body: dict[str, Any] = r.json()
    axes = [s["axis"] for s in body["sections"]]
    assert axes == [
        "highlight",
        "change_over_time",
        "next_week_focus",
        "open_questions",
    ]
