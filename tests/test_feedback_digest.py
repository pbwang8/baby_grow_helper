"""Feedback digest tests for Phase 2.5 trial operations."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from src.core import db as db_module
from src.core import family as family_module
from src.scripts.feedback_digest import (
    FeedbackDigestError,
    SmtpConfig,
    load_feedback,
    render_digest,
    send_email,
    smtp_config_from_env,
)


def _seed_feedback(tmp_db: Path) -> None:
    conn = db_module.get_conn(tmp_db)
    try:
        with db_module.transactional(conn):
            family_module.ensure_family(
                conn,
                family_id="fam_test",
                name="测试家庭",
                access_code="family-secret",
            )
            conn.execute(
                "INSERT INTO children(id, family_id, name, birthday) VALUES (?, ?, ?, ?)",
                ("xiaoming", "fam_test", "小明", "2023-06-01"),
            )
            conn.execute(
                """
                INSERT INTO trial_feedback
                    (id, family_id, child_id, page, category, message, contact, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "fb_digest_1",
                    "fam_test",
                    "xiaoming",
                    "/timeline",
                    "confusing",
                    "时间轴里没有看到刚记录的内容",
                    "tester",
                    "2026-06-24T10:00:00Z",
                ),
            )
    finally:
        conn.close()


def test_load_feedback_from_sqlite(tmp_db: Path) -> None:
    _seed_feedback(tmp_db)
    items = load_feedback(
        since=dt.datetime(2026, 6, 20, tzinfo=dt.UTC),
        until=dt.datetime(2026, 6, 30, tzinfo=dt.UTC),
        backend="sqlite",
    )
    assert len(items) == 1
    assert items[0].family_name == "测试家庭"
    assert items[0].child_name == "小明"
    assert items[0].message == "时间轴里没有看到刚记录的内容"


def test_render_digest_includes_feedback_details(tmp_db: Path) -> None:
    _seed_feedback(tmp_db)
    items = load_feedback(
        since=dt.datetime(2026, 6, 20, tzinfo=dt.UTC),
        until=dt.datetime(2026, 6, 30, tzinfo=dt.UTC),
        backend="sqlite",
    )
    body = render_digest(
        items,
        since=dt.datetime(2026, 6, 20, tzinfo=dt.UTC),
        until=dt.datetime(2026, 6, 30, tzinfo=dt.UTC),
    )
    assert "BabyGrowHelper 内测反馈周报" in body
    assert "反馈数量：1" in body
    assert "测试家庭" in body
    assert "时间轴里没有看到刚记录的内容" in body


def test_render_digest_handles_empty_feedback() -> None:
    body = render_digest(
        [],
        since=dt.datetime(2026, 6, 20, tzinfo=dt.UTC),
        until=dt.datetime(2026, 6, 30, tzinfo=dt.UTC),
    )
    assert "反馈数量：0" in body
    assert "本周期没有新的内测反馈" in body


def test_smtp_config_defaults_to_owner_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BGH_FEEDBACK_DIGEST_TO", raising=False)
    cfg = smtp_config_from_env()
    assert cfg.recipient == "wpb889@outlook.com"


def test_send_email_requires_ready_config() -> None:
    cfg = SmtpConfig(
        host="",
        port=587,
        username="",
        password="",
        sender="",
        recipient="wpb889@outlook.com",
        use_tls=True,
    )
    with pytest.raises(FeedbackDigestError, match="SMTP is not configured"):
        send_email(subject="s", body="b", config=cfg)


def test_send_email_uses_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[object, ...]] = []

    class _FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            sent.append((host, port, timeout))

        def __enter__(self) -> _FakeSMTP:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def starttls(self, *, context: object) -> None:
            sent.append(("tls", context is not None))

        def login(self, username: str, password: str) -> None:
            sent.append(("login", username, password))

        def send_message(self, msg: object) -> None:
            sent.append(("send", msg))

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    cfg = SmtpConfig(
        host="smtp.example.com",
        port=587,
        username="user",
        password="pass",
        sender="sender@example.com",
        recipient="wpb889@outlook.com",
        use_tls=True,
    )
    send_email(subject="周报", body="hello", config=cfg)
    assert sent[0] == ("smtp.example.com", 587, 30)
    assert sent[1][0] == "tls"
    assert sent[2] == ("login", "user", "pass")
    assert sent[3][0] == "send"
