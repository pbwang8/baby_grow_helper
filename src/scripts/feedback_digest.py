"""Weekly trial-feedback digest.

The database is the source of truth. This script only reads `trial_feedback`
and optionally sends a summary email. If SMTP is not configured, it prints the
digest so operators can still inspect feedback without losing data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import os
import smtplib
import ssl
import sys
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, cast

from src.core import db as sqlite_db
from src.core.migrations import detect_backend


class FeedbackDigestError(RuntimeError):
    """Feedback digest cannot be loaded or sent."""


@dataclass(frozen=True)
class FeedbackItem:
    id: str
    family_id: str
    family_name: str
    child_id: str
    child_name: str
    page: str
    category: str
    message: str
    contact: str
    created_at: str


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_tls: bool

    @property
    def ready(self) -> bool:
        return bool(self.host and self.sender and self.recipient)


def load_feedback(
    *,
    since: dt.datetime,
    until: dt.datetime,
    backend: str | None = None,
) -> list[FeedbackItem]:
    resolved = backend or _runtime_backend()
    if resolved == "postgres":
        return _load_feedback_postgres(since=since, until=until)
    if resolved == "sqlite":
        return _load_feedback_sqlite(since=since, until=until)
    raise FeedbackDigestError(f"Unsupported backend: {resolved}")


def render_digest(
    items: list[FeedbackItem], *, since: dt.datetime, until: dt.datetime
) -> str:
    title = "BabyGrowHelper 内测反馈周报"
    lines = [
        title,
        "=" * len(title),
        "",
        f"时间范围：{_fmt_dt(since)} -> {_fmt_dt(until)}",
        f"反馈数量：{len(items)}",
        "",
    ]
    if not items:
        lines.append("本周期没有新的内测反馈。")
        return "\n".join(lines)

    by_category: dict[str, int] = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1
    lines.append("分类汇总：")
    for category, count in sorted(by_category.items()):
        lines.append(f"- {category}: {count}")
    lines.append("")

    for idx, item in enumerate(items, start=1):
        family = item.family_name or item.family_id
        child = item.child_name or item.child_id or "未选择孩子"
        lines.extend(
            [
                f"{idx}. [{item.category}] {item.page}",
                f"   - 时间：{item.created_at}",
                f"   - 家庭：{family} ({item.family_id})",
                f"   - 孩子：{child}",
                f"   - 联系：{item.contact or '未填写'}",
                f"   - 内容：{item.message}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def smtp_config_from_env() -> SmtpConfig:
    username = os.environ.get("SMTP_USERNAME", "").strip()
    sender = os.environ.get("BGH_FEEDBACK_DIGEST_FROM", "").strip() or username
    return SmtpConfig(
        host=os.environ.get("SMTP_HOST", "").strip(),
        port=int(os.environ.get("SMTP_PORT", "587")),
        username=username,
        password=os.environ.get("SMTP_PASSWORD", ""),
        sender=sender,
        recipient=os.environ.get("BGH_FEEDBACK_DIGEST_TO", "wpb889@outlook.com").strip(),
        use_tls=os.environ.get("SMTP_TLS", "1").strip() not in {"0", "false", "False"},
    )


def send_email(*, subject: str, body: str, config: SmtpConfig) -> None:
    if not config.ready:
        raise FeedbackDigestError(
            "SMTP is not configured. Set SMTP_HOST, BGH_FEEDBACK_DIGEST_FROM "
            "or SMTP_USERNAME, and BGH_FEEDBACK_DIGEST_TO."
        )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.sender
    msg["To"] = config.recipient
    msg.set_content(body)

    with smtplib.SMTP(config.host, config.port, timeout=30) as smtp:
        if config.use_tls:
            smtp.starttls(context=ssl.create_default_context())
        if config.username:
            smtp.login(config.username, config.password)
        smtp.send_message(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send weekly trial-feedback digest")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--send", action="store_true", help="Send email via SMTP")
    args = parser.parse_args(argv)

    until = dt.datetime.now(dt.UTC)
    since = until - dt.timedelta(days=args.days)
    items = load_feedback(since=since, until=until)
    body = render_digest(items, since=since, until=until)
    subject = f"BabyGrowHelper 内测反馈周报 ({len(items)} 条)"

    if args.send:
        try:
            send_email(subject=subject, body=body, config=smtp_config_from_env())
        except FeedbackDigestError as e:
            print(body)
            print(f"\nEMAIL_NOT_SENT: {e}", file=sys.stderr)
            return 2
        print(f"Sent feedback digest to {smtp_config_from_env().recipient}")
        return 0

    print(body)
    return 0


def _load_feedback_sqlite(
    *, since: dt.datetime, until: dt.datetime
) -> list[FeedbackItem]:
    conn = sqlite_db.get_conn()
    try:
        rows = conn.execute(
            _FEEDBACK_QUERY_SQLITE,
            (_fmt_dt(since), _fmt_dt(until)),
        ).fetchall()
        return [_item_from_row(_row_to_dict(row)) for row in rows]
    finally:
        conn.close()


def _load_feedback_postgres(
    *, since: dt.datetime, until: dt.datetime
) -> list[FeedbackItem]:
    database_url = os.environ.get("BGH_DATABASE_URL", "")
    if not database_url:
        raise FeedbackDigestError("BGH_DATABASE_URL is required for Postgres digest")
    with _connect_postgres(database_url) as conn, conn.cursor() as cur:
        cur.execute(_FEEDBACK_QUERY_POSTGRES, (since, until))
        return [_item_from_row(_row_to_dict(row)) for row in cur.fetchall()]


def _connect_postgres(database_url: str) -> AbstractContextManager[Any]:
    try:
        psycopg: Any = importlib.import_module("psycopg")
        rows: Any = importlib.import_module("psycopg.rows")
    except ImportError as e:  # pragma: no cover - optional deploy dependency
        raise FeedbackDigestError("Postgres digest requires psycopg") from e
    return cast(
        AbstractContextManager[Any],
        psycopg.connect(database_url, row_factory=rows.dict_row),
    )


def _runtime_backend() -> str:
    explicit = os.environ.get("BGH_RUNTIME_DB_BACKEND", "").strip().lower()
    if explicit in {"sqlite", "postgres"}:
        return explicit
    return detect_backend(os.environ.get("BGH_DATABASE_URL"))


def _item_from_row(row: Mapping[str, object]) -> FeedbackItem:
    return FeedbackItem(
        id=_str(row, "id"),
        family_id=_str(row, "family_id"),
        family_name=_str(row, "family_name"),
        child_id=_str(row, "child_id"),
        child_name=_str(row, "child_name"),
        page=_str(row, "page"),
        category=_str(row, "category"),
        message=_str(row, "message"),
        contact=_str(row, "contact"),
        created_at=_str(row, "created_at"),
    )


def _row_to_dict(row: Mapping[str, object] | object) -> dict[str, object]:
    if isinstance(row, Mapping):
        return dict(row)
    keys = getattr(row, "keys", None)
    if callable(keys):
        row_any: Any = row
        return {str(key): row_any[key] for key in keys()}
    raise FeedbackDigestError(f"Unsupported row type: {type(row).__name__}")


def _str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    return str(value)


def _fmt_dt(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


_FEEDBACK_COLUMNS = """
    tf.id,
    tf.family_id,
    COALESCE(f.name, '') AS family_name,
    COALESCE(tf.child_id, '') AS child_id,
    COALESCE(c.name, '') AS child_name,
    tf.page,
    tf.category,
    tf.message,
    COALESCE(tf.contact, '') AS contact,
    tf.created_at
"""

_FEEDBACK_QUERY_SQLITE = f"""
    SELECT {_FEEDBACK_COLUMNS}
    FROM trial_feedback tf
    LEFT JOIN families f ON f.id = tf.family_id
    LEFT JOIN children c ON c.id = tf.child_id
    WHERE tf.created_at >= ? AND tf.created_at < ?
    ORDER BY tf.created_at DESC, tf.id DESC
"""

_FEEDBACK_QUERY_POSTGRES = f"""
    SELECT {_FEEDBACK_COLUMNS}
    FROM trial_feedback tf
    LEFT JOIN families f ON f.id = tf.family_id
    LEFT JOIN children c ON c.id = tf.child_id
    WHERE tf.created_at >= %s AND tf.created_at < %s
    ORDER BY tf.created_at DESC, tf.id DESC
"""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
