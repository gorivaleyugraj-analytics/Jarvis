"""Mission state machine and supervision (spec §5.3, §7.1.4, §7.7.51).

Enforces legal transitions, step budgets, stale-step reaping and the emergency
stop. Every transition is recorded as a task event and an audit entry.
"""
from __future__ import annotations

from ..core import audit, config, db

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"clarified", "planned", "cancelled"},
    "clarified": {"planned", "cancelled"},
    "needs_user_input": {"planned", "cancelled"},
    "planned": {"running", "paused", "cancelled"},
    "awaiting_approval": {"running", "paused", "cancelled"},
    "running": {"verifying", "paused", "failed", "cancelled", "completed", "awaiting_approval"},
    "verifying": {"completed", "partially_completed", "failed", "running"},
    "paused": {"running", "cancelled"},
    "failed": {"running", "planned", "cancelled"},
    "partially_completed": {"running", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

RUNNABLE_STATES = {"planned", "running", "awaiting_approval", "failed", "partially_completed"}


def get_mission(mission_id: int) -> dict:
    mission = db.one("SELECT * FROM missions WHERE id = ?", (mission_id,))
    if not mission:
        raise ValueError(f"mission {mission_id} not found")
    return mission


def get_steps(mission_id: int) -> list[dict]:
    return db.query("SELECT * FROM mission_steps WHERE mission_id = ? ORDER BY seq", (mission_id,))


def get_events(mission_id: int, limit: int = 200) -> list[dict]:
    return db.query(
        "SELECT * FROM task_events WHERE mission_id = ? ORDER BY id DESC LIMIT ?",
        (mission_id, limit),
    )


def transition(mission_id: int, new_status: str, *, detail: dict | None = None,
               actor: str = "system") -> dict:
    mission = get_mission(mission_id)
    current = mission["status"]
    if new_status == current:
        return mission
    if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal mission transition {current!r} -> {new_status!r}")

    finished = new_status in {"completed", "failed", "cancelled", "partially_completed"}
    db.execute(
        "UPDATE missions SET status = ?, updated_at = ?, "
        "finished_at = CASE WHEN ? THEN ? ELSE finished_at END WHERE id = ?",
        (new_status, db.now_iso(), finished, db.now_iso(), mission_id),
    )
    db.execute(
        "INSERT INTO task_events (mission_id, event, detail_json, created_at) VALUES (?, ?, ?, ?)",
        (mission_id, f"status:{new_status}", db.dumps(detail or {"from": current}), db.now_iso()),
    )
    audit.log_event(actor, "mission.status", subject=f"mission:{mission_id}",
                    outcome=new_status, detail={"from": current, "to": new_status})
    if new_status in {"completed", "failed", "cancelled", "partially_completed"}:
        close_pending_approvals(mission_id)
    return get_mission(mission_id)


def assert_step_budget(mission_id: int) -> None:
    row = db.one("SELECT COUNT(*) AS n FROM mission_steps WHERE mission_id = ?", (mission_id,))
    count = int(row["n"]) if row else 0
    if count > config.settings.max_mission_steps:
        raise ValueError(
            f"mission has {count} steps, exceeding the budget of "
            f"{config.settings.max_mission_steps} (unbounded loops are not allowed)"
        )


def reap_stale_steps(mission_id: int) -> int:
    """Fail steps left 'running' beyond the step timeout (e.g. after a crash)."""
    cutoff = db.now_iso()
    rows = db.query(
        "SELECT id, seq, started_at FROM mission_steps "
        "WHERE mission_id = ? AND status = 'running' AND started_at IS NOT NULL",
        (mission_id,),
    )
    reaped = 0
    for row in rows:
        started = row["started_at"] or ""
        # ISO string comparison is safe: both are UTC ISO-8601 with fixed precision.
        elapsed = _seconds_between(started, cutoff)
        if elapsed > config.settings.step_timeout_s:
            db.execute(
                "UPDATE mission_steps SET status='failed', finished_at=?, "
                "result_json=? WHERE id=?",
                (cutoff, db.dumps({"error": f"step timed out after {elapsed:.0f}s"}), row["id"]),
            )
            db.execute(
                "INSERT INTO task_events (mission_id, step_id, event, detail_json, created_at) "
                "VALUES (?, ?, 'step.timeout', ?, ?)",
                (mission_id, row["id"], db.dumps({"timeout_s": elapsed}), cutoff),
            )
            reaped += 1
    return reaped


def _seconds_between(start_iso: str, end_iso: str) -> float:
    from datetime import datetime

    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        return max(0.0, (end - start).total_seconds())
    except ValueError:
        return 0.0


def pause(mission_id: int, actor: str = "user") -> dict:
    return transition(mission_id, "paused", actor=actor, detail={"reason": "user pause"})


def resume(mission_id: int, actor: str = "user") -> dict:
    return transition(mission_id, "running", actor=actor, detail={"reason": "user resume"})


def cancel(mission_id: int, actor: str = "user", reason: str = "user cancel") -> dict:
    db.execute(
        "UPDATE mission_steps SET status='cancelled', finished_at=? "
        "WHERE mission_id=? AND status IN ('pending','awaiting_approval','running')",
        (db.now_iso(), mission_id),
    )
    return transition(mission_id, "cancelled", actor=actor, detail={"reason": reason})


def close_pending_approvals(mission_id: int) -> int:
    """Cancel approval requests still pending for a finalized mission."""
    row = db.one(
        "SELECT COUNT(*) AS n FROM approval_requests WHERE mission_id=? AND status='pending'",
        (mission_id,),
    )
    count = int(row["n"]) if row else 0
    if count:
        db.execute(
            "UPDATE approval_requests SET status='cancelled', decided_at=? "
            "WHERE mission_id=? AND status='pending'",
            (db.now_iso(), mission_id),
        )
    return count


def emergency_stop(actor: str = "user") -> dict:
    """Cancel everything in flight immediately (spec §10: emergency stop)."""
    affected = db.query(
        "SELECT id FROM missions WHERE status IN ('planned','running','awaiting_approval','paused','verifying')"
    )
    for row in affected:
        cancel(row["id"], actor=actor, reason="emergency stop")
    db.execute(
        "UPDATE approval_requests SET status='cancelled', decided_at=? WHERE status='pending'",
        (db.now_iso(),),
    )
    audit.log_event(actor, "emergency_stop", subject="system", outcome="ok",
                    detail={"missions_cancelled": len(affected)})
    return {"missions_cancelled": len(affected)}
