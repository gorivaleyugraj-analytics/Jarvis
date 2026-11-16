"""Approval gateway (spec §7.7.58, §15 Tier 3).

Sensitive actions are held as pending requests with an expiry. Execution only
proceeds after an explicit approve decision, and every decision is audited.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..core import audit, config, db


def _expiry() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=config.settings.approval_ttl_minutes)
    ).isoformat(timespec="seconds")


def create(
    tool_key: str,
    action: str,
    args: dict,
    *,
    risk_tier: int,
    mission_id: int | None = None,
    step_id: int | None = None,
    reason: str = "",
) -> dict:
    request_id = db.execute(
        "INSERT INTO approval_requests (mission_id, step_id, tool_key, action, args_json, "
        "risk_tier, reason, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (
            mission_id,
            step_id,
            tool_key,
            action,
            db.dumps(args),
            risk_tier,
            reason,
            db.now_iso(),
            _expiry(),
        ),
    )
    audit.log_event(
        "system",
        "approval.created",
        subject=f"approval:{request_id}",
        detail={"tool": tool_key, "risk_tier": risk_tier, "mission_id": mission_id},
    )
    return get(request_id)  # type: ignore[return-value]


def get(request_id: int) -> dict | None:
    return db.one("SELECT * FROM approval_requests WHERE id = ?", (request_id,))


def expire_stale() -> int:
    """Mark pending requests past their expiry; returns how many expired."""
    row = db.one("SELECT COUNT(*) AS n FROM approval_requests WHERE status='pending' AND expires_at < ?",
                 (db.now_iso(),))
    count = int(row["n"]) if row else 0
    if count:
        db.execute(
            "UPDATE approval_requests SET status='expired', decided_at=? "
            "WHERE status='pending' AND expires_at < ?",
            (db.now_iso(), db.now_iso()),
        )
    return count


def pending() -> list[dict]:
    expire_stale()
    return db.query(
        "SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY created_at ASC"
    )


def decide(request_id: int, approved: bool, decided_by: str = "user") -> dict:
    req = get(request_id)
    if not req:
        raise ValueError(f"approval request {request_id} not found")
    if req["status"] != "pending":
        raise ValueError(f"approval request already decided: {req['status']}")
    if req["expires_at"] < db.now_iso():
        db.execute("UPDATE approval_requests SET status='expired', decided_at=? WHERE id=?",
                   (db.now_iso(), request_id))
        raise ValueError("approval request expired")

    new_status = "approved" if approved else "denied"
    db.execute(
        "UPDATE approval_requests SET status=?, decided_at=?, decided_by=? WHERE id=?",
        (new_status, db.now_iso(), decided_by, request_id),
    )
    audit.log_event(
        decided_by,
        "approval.decided",
        subject=f"approval:{request_id}",
        outcome=new_status,
        detail={"tool": req["tool_key"], "mission_id": req["mission_id"]},
    )
    result = get(request_id)
    assert result is not None
    return result
