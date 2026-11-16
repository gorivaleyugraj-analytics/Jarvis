"""Append-only audit trail (spec §7.7.53, §15).

Every consequential action — tool execution, approval decision, mission state
change, memory deletion — lands here with actor, outcome and structured detail.
"""
from __future__ import annotations

from typing import Any

from ..core import db


def log_event(
    actor: str,
    action: str,
    subject: str = "",
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> int:
    return db.execute(
        "INSERT INTO audit_logs (ts, actor, action, subject, outcome, detail_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (db.now_iso(), actor, action, subject, outcome, db.dumps(detail or {})),
    )


def recent(limit: int = 100, action: str | None = None) -> list[dict]:
    limit = max(1, min(limit, 500))
    if action:
        return db.query(
            "SELECT * FROM audit_logs WHERE action = ? ORDER BY id DESC LIMIT ?",
            (action, limit),
        )
    return db.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
