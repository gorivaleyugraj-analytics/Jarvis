"""Health and usage endpoints (spec §14: /api/v1/health, /api/v1/usage).

/health is intentionally unauthenticated so the dashboard and Android app can
detect the backend before login; everything else requires the bearer token.
"""
from __future__ import annotations

import sqlite3
import time

from fastapi import APIRouter, Depends

from ..api.deps import require_auth
from ..core import config, db
from ..providers.router import router as model_router

STARTED_AT = time.time()

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict:
    db_ok = True
    try:
        db.one("SELECT 1 AS ok")
    except sqlite3.Error:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "database": "ok" if db_ok else "error",
        "provider": model_router.describe(),
    }


@router.get("/usage", dependencies=[Depends(require_auth)])
def usage() -> dict:
    today = db.now_iso()[:10]
    totals = db.one(
        "SELECT COUNT(*) AS requests, COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
        "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
        "COALESCE(SUM(cost_usd),0) AS cost_usd "
        "FROM model_usage WHERE substr(ts,1,10) = ?",
        (today,),
    ) or {}
    by_provider = db.query(
        "SELECT provider, model, COUNT(*) AS requests, COALESCE(SUM(cost_usd),0) AS cost_usd "
        "FROM model_usage WHERE substr(ts,1,10) = ? GROUP BY provider, model",
        (today,),
    )
    counts = {
        "conversations": (db.one("SELECT COUNT(*) AS n FROM conversations") or {}).get("n", 0),
        "missions": (db.one("SELECT COUNT(*) AS n FROM missions") or {}).get("n", 0),
        "memories": (db.one("SELECT COUNT(*) AS n FROM memories") or {}).get("n", 0),
        "audit_events": (db.one("SELECT COUNT(*) AS n FROM audit_logs") or {}).get("n", 0),
    }
    limit = config.settings.daily_cost_limit_usd
    return {
        "today": totals,
        "by_provider": by_provider,
        "counts": counts,
        "budget": {
            "daily_limit_usd": limit,
            "spent_today_usd": model_router.cost_today(),
            "within_budget": limit <= 0 or model_router.cost_today() < limit,
        },
    }
