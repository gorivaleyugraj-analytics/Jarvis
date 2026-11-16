"""Mission and approval endpoints (spec §14: /api/v1/missions, /approvals).

Creating a mission plans it and immediately runs the safe steps; Tier 3 steps
park the mission in `awaiting_approval` and surface an approval request.
Approving resumes execution automatically; denying marks the step cancelled
and the mission partially completed/cancelled — the action is never executed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..api.deps import require_auth
from ..core import approvals, audit, db
from ..orchestrator import executor, supervisor
from ..orchestrator.planner import create_mission
from ..tools.base import ToolContext, ToolResult
from ..tools.registry import register_builtin_tools, registry

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


class MissionCreate(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str = "user"


def _mission_brief(mission_id: int) -> dict:
    mission = supervisor.get_mission(mission_id)
    steps = supervisor.get_steps(mission_id)
    return {
        **mission,
        "steps": steps,
        "pending_approvals": [
            a for a in approvals.pending() if a["mission_id"] == mission_id
        ],
    }


@router.post("/missions")
def create_mission_endpoint(req: MissionCreate, _token: str = Depends(require_auth)) -> dict:
    created = create_mission(req.goal)
    mission_id = created["mission"]["id"]
    if created["mission"]["status"] == "planned":
        return executor.run_mission(mission_id)
    return {
        "mission": created["mission"],
        "steps": created["steps"],
        "events": supervisor.get_events(mission_id, limit=10),
        "stopped_reason": created["clarification"] or "",
    }


@router.get("/missions")
def list_missions(_token: str = Depends(require_auth)) -> list[dict]:
    return db.query(
        "SELECT m.*, "
        "(SELECT COUNT(*) FROM mission_steps s WHERE s.mission_id = m.id) AS step_count, "
        "(SELECT COUNT(*) FROM mission_steps s WHERE s.mission_id = m.id AND s.status = 'completed') "
        "AS completed_steps "
        "FROM missions m ORDER BY m.id DESC LIMIT 100"
    )


@router.get("/missions/{mission_id}")
def get_mission(mission_id: int, _token: str = Depends(require_auth)) -> dict:
    return {
        **_mission_brief(mission_id),
        "events": supervisor.get_events(mission_id),
    }


@router.post("/missions/{mission_id}/run")
def run_mission(mission_id: int, _token: str = Depends(require_auth)) -> dict:
    return executor.run_mission(mission_id)


@router.post("/missions/{mission_id}/pause")
def pause_mission(mission_id: int, _token: str = Depends(require_auth)) -> dict:
    return supervisor.pause(mission_id)


@router.post("/missions/{mission_id}/resume")
def resume_mission(mission_id: int, _token: str = Depends(require_auth)) -> dict:
    supervisor.resume(mission_id)
    return executor.run_mission(mission_id)


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(mission_id: int, _token: str = Depends(require_auth)) -> dict:
    return supervisor.cancel(mission_id)


@router.get("/approvals")
def list_approvals(_token: str = Depends(require_auth)) -> dict:
    recent = db.query(
        "SELECT * FROM approval_requests WHERE status != 'pending' "
        "ORDER BY id DESC LIMIT 25"
    )
    return {"pending": approvals.pending(), "recent": recent}


def _execute_approved(decision: dict) -> dict:
    """Run a manually-invoked tool whose approval the user just granted."""
    register_builtin_tools()
    tool = registry.get(decision["tool_key"])
    if tool is None:
        return {"success": False, "action": decision["tool_key"],
                "summary": "Tool no longer registered", "error": "unknown tool"}
    args = db.loads(decision["args_json"], {}) or {}
    ctx = ToolContext(
        user_id=db.default_user_id(),
        actor="dashboard",
        approved_request_id=int(decision["id"]),
    )
    try:
        result = tool.run(args, ctx)
    except Exception as exc:
        result = ToolResult(success=False, action=tool.key,
                            summary="Approved execution failed", error=str(exc))
    audit.log_event("dashboard", "tool.executed", subject=tool.key,
                    outcome="ok" if result.success else "failed",
                    detail={"approved_request": decision["id"], "summary": result.summary})
    return result.to_dict()


@router.post("/approvals/{request_id}/decide")
def decide_approval(request_id: int, req: ApprovalDecision,
                    _token: str = Depends(require_auth)) -> dict:
    decision = approvals.decide(request_id, req.approved, req.decided_by)
    mission_id = decision["mission_id"]
    # NOTE: mission steps deliberately stay 'awaiting_approval' — the executor
    # reads the approved request and attaches it as execution authorization.
    if mission_id:
        try:
            report: dict | None = executor.run_mission(int(mission_id))
        except ValueError as exc:
            report = {"note": f"mission not runnable: {exc}"}
        return {"approval": decision, "mission_report": report}
    if req.approved:
        return {"approval": decision, "result": _execute_approved(decision)}
    return {"approval": decision, "mission_report": None}


@router.post("/emergency-stop")
def emergency_stop(_token: str = Depends(require_auth)) -> dict:
    return supervisor.emergency_stop()
