"""Mission executor (spec §7.1.3/§7.1.4, §16).

Runs mission steps through the policy engine and tool registry with bounded
retries, checkpoints (task events) and verification evidence. A step is only
'completed' with evidence; a mission is only 'completed' when every step
completed. Tier 3 actions stop the mission in `awaiting_approval` until the
user decides — the executor never executes them on its own.
"""
from __future__ import annotations

import json

from ..core import audit, config, db
from ..core.approvals import create as create_approval, get as get_approval
from ..core.approvals import expire_stale as expire_stale_approvals
from ..core.logging_setup import get_logger
from ..core.policy import PolicyError, decide
from ..tools.base import ToolContext, ToolError
from ..tools.registry import register_builtin_tools, registry
from . import supervisor
from .supervisor import close_pending_approvals

log = get_logger("executor")


def _resolve_args(args: dict, previous: dict | None) -> dict:
    """Substitute {{prev_json}} / {{prev_summary}} with the previous step's result."""
    if not previous:
        return args
    resolved = {}
    for key, value in args.items():
        if isinstance(value, str):
            value = value.replace("{{prev_json}}", json.dumps(previous.get("output"), default=str)[:4000])
            value = value.replace("{{prev_summary}}", str(previous.get("summary") or ""))
        resolved[key] = value
    return resolved


def _step_by_seq(steps: list[dict], seq: int | None) -> dict | None:
    if seq is None:
        return None
    return next((s for s in steps if s["seq"] == seq), None)


def _deps_satisfied(step: dict, steps: list[dict], state: dict[int, dict]) -> bool:
    dep = _step_by_seq(steps, step["depends_on"])
    if dep is None:
        return True
    return state.get(dep["id"], dep).get("status") == "completed"


def _record_event(mission_id: int, step_id: int | None, event: str, detail: dict) -> None:
    db.execute(
        "INSERT INTO task_events (mission_id, step_id, event, detail_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (mission_id, step_id, event, db.dumps(detail), db.now_iso()),
    )


def _finish_step(step: dict, status: str, result: dict, verification: dict | None) -> None:
    db.execute(
        "UPDATE mission_steps SET status=?, attempts=attempts+1, result_json=?, verification=?, "
        "started_at=COALESCE(started_at, ?), finished_at=? WHERE id=?",
        (status, db.dumps(result), db.dumps(verification) if verification else None,
         db.now_iso(), db.now_iso(), step["id"]),
    )


def run_mission(mission_id: int, *, actor: str = "api") -> dict:
    register_builtin_tools()
    expire_stale_approvals()
    mission = supervisor.get_mission(mission_id)
    if mission["status"] not in supervisor.RUNNABLE_STATES:
        raise ValueError(f"mission in state '{mission['status']}' cannot run")
    supervisor.assert_step_budget(mission_id)
    supervisor.reap_stale_steps(mission_id)
    if mission["status"] != "running":
        mission = supervisor.transition(mission_id, "running", actor=actor)

    steps = supervisor.get_steps(mission_id)
    # Live status mirror: dependency checks must see updates made during this run.
    state: dict[int, dict] = {s["id"]: dict(s) for s in steps}
    completed: list[dict] = []
    stopped_reason = ""

    for step in steps:
        if step["status"] in {"completed", "skipped", "cancelled"}:
            if step["status"] == "completed":
                completed.append(step)
            continue
        if not _deps_satisfied(step, steps, state):
            continue

        tool = registry.get(step["tool_key"])
        if tool is None:
            _finish_step(step, "failed", {"error": f"unknown tool {step['tool_key']}"}, None)
            _record_event(mission_id, step["id"], "step.failed",
                          {"error": f"unknown tool {step['tool_key']}"})
            mission = supervisor.transition(mission_id, "failed",
                                            detail={"reason": "unknown tool"})
            stopped_reason = "unknown tool in plan"
            break

        if int(step["attempts"] or 0) >= config.settings.max_tool_retries:
            mission = supervisor.transition(mission_id, "failed",
                                            detail={"reason": "retry budget exhausted"})
            stopped_reason = "retry budget exhausted"
            break

        args = db.loads(step["args_json"], {}) or {}
        previous = _step_by_seq(steps, step["depends_on"])
        args = _resolve_args(args, previous)
        ctx = ToolContext(
            user_id=int(mission["user_id"]),
            actor=actor,
            mission_id=mission_id,
            step_id=step["id"],
        )

        # -- approval gate -------------------------------------------------
        if step["status"] == "awaiting_approval":
            request = get_approval(int(step["approval_id"])) if step["approval_id"] else None
            if request is None or request["status"] == "expired":
                db.execute("UPDATE mission_steps SET status='pending', approval_id=NULL WHERE id=?",
                           (step["id"],))
                step = {**step, "status": "pending", "approval_id": None}
                state[step["id"]]["status"] = "pending"
            elif request["status"] == "approved":
                ctx.approved_request_id = int(request["id"])
            elif request["status"] == "denied":
                _finish_step(step, "cancelled", {"error": "user denied approval"}, None)
                state[step["id"]]["status"] = "cancelled"
                _record_event(mission_id, step["id"], "approval.denied",
                              {"tool": step["tool_key"]})
                stopped_reason = "user denied a sensitive action"
                mission = supervisor.transition(
                    mission_id,
                    "partially_completed" if completed else "cancelled",
                    detail={"reason": "user denied a sensitive action"},
                )
                break
            else:  # still pending -> keep the mission waiting
                mission = supervisor.transition(mission_id, "awaiting_approval",
                                                detail={"approval_id": step["approval_id"]})
                stopped_reason = "waiting for user approval"
                break

        decision = decide(tool, args, ctx)
        if decision.requires_approval:
            request = create_approval(
                tool.key,
                tool.name,
                args,
                risk_tier=decision.tier,
                mission_id=mission_id,
                step_id=step["id"],
                reason=decision.reason,
            )
            db.execute(
                "UPDATE mission_steps SET status='awaiting_approval', approval_id=? WHERE id=?",
                (request["id"], step["id"]),
            )
            _record_event(mission_id, step["id"], "approval.required",
                          {"tool": tool.key, "approval_id": request["id"],
                           "risk_tier": decision.tier})
            mission = supervisor.transition(mission_id, "awaiting_approval",
                                            detail={"approval_id": request["id"]})
            stopped_reason = "sensitive action needs approval"
            break
        if not decision.allowed:
            _finish_step(step, "failed", {"error": decision.reason}, None)
            _record_event(mission_id, step["id"], "step.blocked", {"reason": decision.reason})
            mission = supervisor.transition(mission_id, "failed",
                                            detail={"reason": decision.reason})
            stopped_reason = decision.reason
            break

        # -- execution with bounded retries --------------------------------
        db.execute(
            "UPDATE mission_steps SET status='running', started_at=? WHERE id=?",
            (db.now_iso(), step["id"]),
        )
        step = {**step, "status": "running"}
        state[step["id"]]["status"] = "running"
        result = None
        error: str | None = None
        for attempt in range(config.settings.max_tool_retries):
            try:
                result = tool.run(args, ctx)
                error = None
                break
            except (ToolError, PolicyError) as exc:
                error = str(exc)
                log.warning("tool %s failed (attempt %s): %s", tool.key, attempt + 1, error)
            except Exception as exc:  # unexpected tool crash — still bounded
                error = f"unexpected tool error: {exc}"
                log.exception("tool %s crashed", tool.key)
        if result is None:
            _finish_step(step, "failed", {"error": error}, None)
            _record_event(mission_id, step["id"], "step.failed",
                          {"tool": tool.key, "error": error,
                           "attempts": config.settings.max_tool_retries})
            audit.log_event(actor, "tool.executed", subject=tool.key, outcome="failed",
                            detail={"mission_id": mission_id, "error": error})
            mission = supervisor.transition(mission_id, "failed", detail={"reason": error})
            stopped_reason = f"tool {tool.key} failed: {error}"
            break

        verification = result.verification or {"verified": False, "reason": "tool returned no evidence"}
        final_status = "completed" if result.success else "failed"
        _finish_step(step, final_status, result.to_dict(), verification)
        state[step["id"]]["status"] = final_status
        _record_event(
            mission_id, step["id"],
            "step.completed" if result.success else "step.failed",
            {"tool": tool.key, "summary": result.summary, "verification": verification},
        )
        audit.log_event(actor, "tool.executed", subject=tool.key,
                        outcome="ok" if result.success else "failed",
                        detail={"mission_id": mission_id, "step_id": step["id"],
                                "summary": result.summary})
        if result.success:
            completed.append({**step, "status": "completed"})
        else:
            mission = supervisor.transition(mission_id, "failed",
                                            detail={"reason": result.error or "tool failed"})
            stopped_reason = result.error or "tool failed"
            break

    # -- final verification pass (spec §16: never claim unverified success) --
    steps = supervisor.get_steps(mission_id)
    if not stopped_reason and all(s["status"] == "completed" for s in steps) and steps:
        mission = supervisor.transition(mission_id, "verifying", actor=actor)
        unverified = [s["seq"] for s in steps
                      if not (db.loads(s["verification"], {}) or {}).get("verified", True)]
        if unverified:
            mission = supervisor.transition(
                mission_id, "partially_completed",
                detail={"reason": "steps without verification evidence", "unverified": unverified},
            )
        else:
            mission = supervisor.transition(mission_id, "completed", actor=actor)
        db.execute(
            "UPDATE missions SET result_summary=? WHERE id=?",
            (f"All {len(steps)} step(s) completed and verified.", mission_id),
        )
    elif not stopped_reason:
        pending_left = [s["seq"] for s in steps if s["status"] in {"pending", "awaiting_approval"}]
        if pending_left and completed:
            mission = supervisor.transition(mission_id, "paused",
                                            detail={"pending_steps": pending_left})

    mission = supervisor.get_mission(mission_id)
    return {
        "mission": mission,
        "steps": supervisor.get_steps(mission_id),
        "events": supervisor.get_events(mission_id, limit=20),
        "stopped_reason": stopped_reason,
    }
