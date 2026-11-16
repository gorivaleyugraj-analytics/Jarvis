"""Memory and audit endpoints (spec §14: /memory, /audit).

Memory is fully user-controlled: add, correct, delete one, delete all, export
everything. Deletions are audited (spec §7.2.14).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..api.deps import require_auth
from ..core import audit
from ..memory import store as memory_store

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: str = "fact"


class MemoryUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    kind: str | None = None


@router.get("/memory")
def search_memories(q: str = "", kind: str | None = None, limit: int = 50,
                    _token: str = Depends(require_auth)) -> list[dict]:
    try:
        return memory_store.search(q, kind=kind, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/memory")
def add_memory(req: MemoryCreate, _token: str = Depends(require_auth)) -> dict:
    try:
        memory = memory_store.add(req.content, kind=req.kind, source="user")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.log_event("user", "memory.created", subject=f"memory:{memory['id']}")
    return memory


@router.patch("/memory/{memory_id}")
def update_memory(memory_id: int, req: MemoryUpdate,
                  _token: str = Depends(require_auth)) -> dict:
    try:
        memory = memory_store.update(memory_id, req.content, kind=req.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    audit.log_event("user", "memory.corrected", subject=f"memory:{memory_id}")
    return memory


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: int, _token: str = Depends(require_auth)) -> dict:
    if not memory_store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    audit.log_event("user", "memory.deleted", subject=f"memory:{memory_id}")
    return {"deleted": memory_id}


@router.delete("/memory")
def delete_all_memories(_token: str = Depends(require_auth)) -> dict:
    count = memory_store.delete_all()
    audit.log_event("user", "memory.deleted_all", outcome="ok", detail={"count": count})
    return {"deleted": count}


@router.get("/memory/export")
def export_memories(_token: str = Depends(require_auth)) -> dict:
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": memory_store.export_all(),
    }


@router.get("/audit")
def audit_trail(limit: int = 100, action: str | None = None,
                _token: str = Depends(require_auth)) -> list[dict]:
    return audit.recent(limit=limit, action=action)
