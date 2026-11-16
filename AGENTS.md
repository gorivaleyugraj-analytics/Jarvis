# JARVIS X — Agent & System Notes

## Quickstart
```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/uvicorn backend.main:app --port 8000
# Dashboard at http://127.0.0.1:8000
```

## Test suite
```bash
venv/bin/python -m pytest tests/ -q   # 29 tests
```

## Key design notes (spec §15/§16/§21)
- **Tier 1** (read/analyze) runs autonomously; **Tier 2** reversible actions run autonomously when `JARVIS_AUTONOMOUS_TIER2=true`; **Tier 3** sensitive actions always require explicit approval via `/api/v1/approvals/{id}/decide`.
- Tool paths are sandboxed: `resolve_within_workspace()` rejects absolute paths and `..` escapes. Every filesystem tool validates against the configured `JARVIS_WORKSPACE_DIR`.
- Structured `ToolResult` carries success/failure, summary, output, error, and verification evidence — the orchestrator never confuses an attempt with a completed result.
- Bounded retries: `max_tool_retries=3` (configurable); after exhaustion the step fails and the mission may continue or fail depending on criticality.
- Model router (`backend/providers/router.py`) auto-falls back from OpenAI-compat to the offline rule-based provider when the API key is missing, the daily cost limit is reached, or the `privacy=local` flag is set.
- Mission state machine (`created → clarified → planned → running → verifying → completed/failed/cancelled/needs_user_input`) enforced by `backend/orchestrator/supervisor.py`; emergency stop cancels all in-flight missions and pending approvals.
- 11 specialist agents are seeded in `backend/agents/registry.py` with per-agent tool allowlists (least‑privilege principle).
- Memory (`backend/memory/store.py`) supports add/search/correct/export/delete with a full audit trail in `audit_logs`.
- Dashboard (`dashboard/`) is a phone‑first, dark‑mode SPA with chat, missions, approvals, agents, tools, memory and audit panels.

## Adding a new builtin tool
1. Subclass `Tool` in `backend/tools/<name>.py` (define `key`, `name`, `risk_tier`, `schema`, `run()`).
2. Add the tool instance to `TOOLS` tuple in the module.
3. Call `register_builtin_tools()` once at startup (from `backend/main.py`).
4. The tool auto‑appears in `/api/v1/tools`, the dashboard, and the agent allowlists.

## Adding a new agent
1. Add a dict to `AGENTS` in `backend/agents/registry.py` with `key`, `name`, `description`, `tools` (allowlist).
2. Run `venv/bin/python -c "from backend.agents.registry import sync_to_db; sync_to_db()"` to persist.
3. The agent appears at `GET /api/v1/agents`.