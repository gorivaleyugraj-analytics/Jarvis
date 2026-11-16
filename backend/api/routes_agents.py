"""Agent, tool and skill endpoints (spec §14: /agents, /tools, /skills).

Manual tool invocation goes through the exact same policy engine as missions:
a Tier 3 call from the dashboard still produces an approval request instead of
executing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..agents import registry as agents_registry
from ..api.deps import require_auth
from ..core import audit, db
from ..core.approvals import create as create_approval
from ..core.policy import PolicyError, decide, describe_tiers
from ..tools.base import ToolContext, ToolError
from ..tools.registry import register_builtin_tools, registry

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


class ToolInvokeRequest(BaseModel):
    args: dict = {}


@router.get("/agents")
def list_agents(_token: str = Depends(require_auth)) -> list[dict]:
    return agents_registry.list_agents()


@router.get("/agents/{key}")
def get_agent(key: str, _token: str = Depends(require_auth)) -> dict:
    agent = agents_registry.get_agent(key)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/tools")
def list_tools(_token: str = Depends(require_auth)) -> dict:
    register_builtin_tools()
    return {"tools": registry.metas(), "tiers": describe_tiers()}


@router.post("/tools/{key}/invoke")
def invoke_tool(key: str, req: ToolInvokeRequest,
                _token: str = Depends(require_auth)) -> dict:
    register_builtin_tools()
    try:
        tool = registry.require(key)
        args = registry.validate_args(tool, req.args)
    except (ToolError, PolicyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    ctx = ToolContext(user_id=db.default_user_id(), actor="dashboard")
    decision = decide(tool, args, ctx)
    if decision.requires_approval:
        request = create_approval(
            tool.key, tool.name, args, risk_tier=decision.tier,
            reason=f"manual invocation: {decision.reason}",
        )
        audit.log_event("dashboard", "tool.invocation.deferred", subject=tool.key,
                        outcome="approval_required", detail={"approval_id": request["id"]})
        return {
            "status": "approval_required",
            "reason": decision.reason,
            "approval": request,
        }
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)

    try:
        result = tool.run(args, ctx)
    except PolicyError as exc:
        audit.log_event("dashboard", "tool.blocked", subject=key, outcome="denied",
                        detail={"error": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:  # structured failure, never a leaked 500
        audit.log_event("dashboard", "tool.executed", subject=key, outcome="failed",
                        detail={"error": str(exc)})
        return {"status": "executed", "result": {"success": False, "action": key,
                                                 "summary": "Tool failed", "error": str(exc)}}
    audit.log_event("dashboard", "tool.executed", subject=key,
                    outcome="ok" if result.success else "failed",
                    detail={"summary": result.summary})
    return {"status": "executed", "result": result.to_dict()}


@router.get("/skills")
def list_skills(_token: str = Depends(require_auth)) -> list[dict]:
    return db.query("SELECT * FROM skills ORDER BY key")
