"""Local productivity tools: notes, reminders, device status and a
network-degradable web search stub (spec §7.5.31 — honest about offline mode).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..core import config, db
from ..core.policy import resolve_within_workspace
from .base import TIER_READ, TIER_REVERSIBLE, Tool, ToolContext, ToolResult, truncate

NOTES_DIR = "notes"


class NotesCreate(Tool):
    key = "notes.create"
    name = "Create note"
    description = "Write a markdown note into the workspace notes folder."
    risk_tier = TIER_REVERSIBLE
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
        "required": ["title", "content"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        title = str(args["title"]).strip()[:80]
        safe_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-").lower() or "note"
        notes_dir = resolve_within_workspace(NOTES_DIR)
        notes_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = notes_dir / f"{stamp}-{safe_slug}.md"
        body = f"# {title}\n\n{args['content']}\n"
        path.write_text(body, encoding="utf-8")
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Created note '{title}'",
            output={"file": str(path.relative_to(config.settings.workspace_dir)), "bytes": path.stat().st_size},
            verification={"file_exists": path.is_file(), "bytes_on_disk": path.stat().st_size},
        )


class NotesSearch(Tool):
    key = "notes.search"
    name = "Search notes"
    description = "Full-text search across note files in the workspace."
    risk_tier = TIER_READ
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        query = str(args["query"]).strip().lower()
        notes_dir = resolve_within_workspace(NOTES_DIR)
        matches: list[dict] = []
        if notes_dir.is_dir() and query:
            for path in sorted(notes_dir.glob("*.md")):
                text = path.read_text(encoding="utf-8", errors="replace")
                lowered = text.lower()
                if query in lowered:
                    idx = lowered.find(query)
                    snippet = text[max(0, idx - 60): idx + 120].replace("\n", " ")
                    matches.append({"file": path.name, "snippet": snippet.strip()})
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Found {len(matches)} note(s) matching '{args['query']}'",
            output={"query": args["query"], "matches": matches},
            verification={"notes_scanned": len(list(notes_dir.glob('*.md'))) if notes_dir.is_dir() else 0},
        )


def _parse_when(raw: str) -> str:
    """Accept ISO-8601, '+30m'/'+2h'/'+1d' offsets or 'tomorrow'."""
    raw = raw.strip()
    now = datetime.now(timezone.utc)
    offset = re.fullmatch(r"\+(\d+)\s*([mhd])", raw.lower())
    if offset:
        amount, unit = int(offset.group(1)), offset.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return (now + delta).isoformat(timespec="seconds")
    if raw.lower() == "tomorrow":
        return (now + timedelta(days=1)).isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat(timespec="seconds")
    except ValueError as exc:
        raise ToolError(f"cannot parse 'when' value: {raw!r} (use ISO-8601, +30m/+2h/+1d or 'tomorrow')") from exc


class ReminderCreate(Tool):
    key = "reminder.create"
    name = "Create reminder"
    description = "Schedule a reminder as a durable scheduled job."
    risk_tier = TIER_REVERSIBLE
    schema = {
        "type": "object",
        "properties": {"message": {"type": "string"}, "when": {"type": "string"}},
        "required": ["message", "when"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        run_at = _parse_when(str(args["when"]))
        job_id = db.execute(
            "INSERT INTO scheduled_jobs (kind, payload_json, run_at, status, created_at) "
            "VALUES ('reminder', ?, ?, 'scheduled', ?)",
            (db.dumps({"message": str(args["message"])}), run_at, db.now_iso()),
        )
        row = db.one("SELECT id, run_at, status FROM scheduled_jobs WHERE id = ?", (job_id,))
        return ToolResult(
            success=True,
            action=self.key,
            summary=f"Reminder scheduled for {run_at}",
            output={"job": row, "message": str(args["message"])},
            verification={"job_id": job_id, "persisted": row is not None},
        )


class SystemStatus(Tool):
    key = "system.status"
    name = "Device / system status"
    description = "Report backend health, storage usage and task counts (read-only)."
    risk_tier = TIER_READ
    schema = {"type": "object", "properties": {}, "required": []}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        db_path: Path = config.settings.db_path
        missions = db.query("SELECT status, COUNT(*) AS n FROM missions GROUP BY status")
        pending = db.one("SELECT COUNT(*) AS n FROM approval_requests WHERE status = 'pending'") or {"n": 0}
        workspace_files = sum(1 for _ in config.settings.workspace_dir.rglob("*") if _.is_file())
        output = {
            "python": sys.version.split()[0],
            "database_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "workspace_files": workspace_files,
            "missions_by_status": {r["status"]: r["n"] for r in missions},
            "pending_approvals": pending["n"],
            "provider_mode": config.settings.provider_mode,
        }
        return ToolResult(
            success=True,
            action=self.key,
            summary="System status collected",
            output=output,
            verification={"checked": db.now_iso()},
        )


class WebSearch(Tool):
    key = "web.search"
    name = "Web search"
    description = (
        "Search the public web. Requires JARVIS_WEB_SEARCH_ENABLED=true and network "
        "access; otherwise returns an honest 'unavailable' result instead of guessing."
    )
    risk_tier = TIER_READ
    timeout_s = 20
    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        "required": ["query"],
    }

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        import os

        import httpx

        query = str(args["query"]).strip()
        max_results = int(args.get("max_results") or 5)
        if os.getenv("JARVIS_WEB_SEARCH_ENABLED", "").lower() not in {"1", "true", "yes"}:
            return ToolResult(
                success=False,
                action=self.key,
                summary="Web search unavailable (offline / not configured)",
                output={
                    "query": query,
                    "results": [],
                    "reason": "JARVIS_WEB_SEARCH_ENABLED is not enabled; refusing to fabricate sources",
                },
                error="web search not configured",
                verification={"verified": False, "reason": "disabled"},
            )
        try:
            response = httpx.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
                timeout=15,
                headers={"User-Agent": "jarvis-x/0.1 (+personal assistant)"},
            )
            response.raise_for_status()
        except Exception as exc:  # network errors are expected, not crashes
            return ToolResult(
                success=False,
                action=self.key,
                summary="Web search failed (network error)",
                output={"query": query, "results": []},
                error=str(exc),
                verification={"verified": False, "reason": "network_error"},
            )
        links = re.findall(r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', response.text)
        results = []
        for url, title in links[:max_results]:
            clean = re.sub(r"<[^>]+>", "", title).strip()
            results.append({"title": clean, "url": url})
        return ToolResult(
            success=bool(results),
            action=self.key,
            summary=f"Found {len(results)} result(s) for '{query}'",
            output={"query": query, "results": results},
            verification={"source": "lite.duckduckgo.com", "result_count": len(results)},
        )


TOOLS: tuple[Tool, ...] = (
    NotesCreate(),
    NotesSearch(),
    ReminderCreate(),
    SystemStatus(),
    WebSearch(),
)
