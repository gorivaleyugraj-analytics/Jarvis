"""SQLite access layer for JARVIS X.

One short-lived connection per operation (cheap for SQLite), WAL journal and
foreign keys enforced on every connection. Small helpers (query/one/execute)
keep call sites terse and consistent.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config
from .logging_setup import get_logger

log = get_logger("db")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.settings.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db(commit: bool = False) -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: tuple | list = ()) -> int:
    """Run a write statement, commit, return lastrowid."""
    with db(commit=True) as conn:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid or 0)


def executemany(sql: str, seq: list[tuple | list]) -> None:
    with db(commit=True) as conn:
        conn.executemany(sql, seq)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def init_db() -> None:
    """Create the schema if missing and seed the baseline rows."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with db(commit=True) as conn:
        conn.executescript(schema)

    if not one("SELECT id FROM users WHERE username = ?", ("owner",)):
        execute(
            "INSERT INTO users (username, display_name, created_at) VALUES (?, ?, ?)",
            ("owner", "JARVIS Owner", now_iso()),
        )
        log.info("seeded default user 'owner'")

    for key, value in (
        ("installed_at", now_iso()),
        ("schema_version", "1"),
    ):
        execute(
            "INSERT INTO system_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, value, now_iso()),
        )


def get_setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM system_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO system_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_iso()),
    )


def default_user_id() -> int:
    row = one("SELECT id FROM users WHERE username = ?", ("owner",))
    return int(row["id"]) if row else 1
