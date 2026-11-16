"""Specialist agent registry (spec §8).

Agents do NOT get unrestricted tool access: each one carries an explicit
allowlist, and the registry reports which tools are actually available so the
dashboard can show real capability instead of promises.
"""
from __future__ import annotations

from ..core import db
from ..tools.registry import register_builtin_tools, registry

AGENTS: list[dict] = [
    {"key": "research", "name": "Research Agent",
     "description": "Web research, source comparison and citations.",
     "tools": ["web.search", "notes.create", "notes.search"]},
    {"key": "android", "name": "Android Agent",
     "description": "Device operations through approved Android tools (Phase 4 bridge).",
     "tools": ["system.status"]},
    {"key": "browser", "name": "Browser Agent",
     "description": "Supported website workflows (Phase 7: Playwright on a host).",
     "tools": []},
    {"key": "file", "name": "File Agent",
     "description": "File search, organization and document operations.",
     "tools": ["fs.list", "fs.read", "fs.write", "fs.organize", "fs.delete"]},
    {"key": "coding", "name": "Coding Agent",
     "description": "Code generation, explanation, refactoring and debugging (Phase 9).",
     "tools": []},
    {"key": "testing", "name": "Testing Agent",
     "description": "Test execution and result analysis (Phase 9).",
     "tools": []},
    {"key": "devops", "name": "DevOps Agent",
     "description": "Deployment preparation, logs and monitoring (Phase 7+).",
     "tools": []},
    {"key": "assistant", "name": "Personal Assistant Agent",
     "description": "Reminders, notes and task coordination.",
     "tools": ["reminder.create", "notes.create", "notes.search", "system.status"]},
    {"key": "data", "name": "Data Agent",
     "description": "Spreadsheet, database and log analysis (Phase 9).",
     "tools": []},
    {"key": "vision", "name": "Vision Agent",
     "description": "Image and screenshot interpretation (Phase 9).",
     "tools": []},
    {"key": "security", "name": "Security Agent",
     "description": "Permission and policy checks; reviews audit trail.",
     "tools": ["system.status"]},
]


def sync_to_db() -> None:
    for agent in AGENTS:
        db.execute(
            "INSERT INTO agents (key, name, description, tools_json, status) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET name=excluded.name, description=excluded.description, "
            "tools_json=excluded.tools_json, status=excluded.status",
            (agent["key"], agent["name"], agent["description"], db.dumps(agent["tools"]),
             "ready" if agent["tools"] else "planned"),
        )


def list_agents() -> list[dict]:
    register_builtin_tools()
    known = {a["key"]: a for a in AGENTS}
    result = []
    for row in db.query("SELECT * FROM agents ORDER BY key"):
        allowlist = db.loads(row["tools_json"], []) or []
        tools = []
        for key in allowlist:
            tool = registry.get(key)
            tools.append({**tool.meta()} if tool else {"key": key, "missing": True})
        result.append({
            "key": row["key"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"] if row["status"] != "ready" or tools else "planned",
            "tools": tools,
        })
    # Surface any agent present only in code (fresh DB safety).
    for key, agent in known.items():
        if not any(r["key"] == key for r in result):
            result.append({**agent, "status": "ready" if agent["tools"] else "planned", "tools": []})
    return result


def get_agent(key: str) -> dict | None:
    agents = list_agents()
    return next((a for a in agents if a["key"] == key), None)
