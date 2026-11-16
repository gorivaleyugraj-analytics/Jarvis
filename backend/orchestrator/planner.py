"""Mission planner (spec §7.1.2, §5.2).

Decomposes a natural-language goal into concrete tool steps with dependencies.
Uses the model provider for planning when one is configured, and falls back to
deterministic templates otherwise — so the system always produces a valid,
registry-checked plan. Missing essential information (e.g. a reminder time)
puts the mission into `needs_user_input` instead of guessing (§5.2.1).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core import audit, config, db
from ..core.logging_setup import get_logger
from ..providers.base import ChatMessage, ProviderError
from ..providers.router import router as model_router
from ..tools.base import ToolError
from ..tools.registry import register_builtin_tools, registry

log = get_logger("planner")

TIME_EXPR = re.compile(
    r"(\+\s*\d+\s*[mhd]\b|\btomorrow\b|\btonight\b|\bnext week\b"
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b)",
    re.I,
)


@dataclass
class StepSpec:
    seq: int
    title: str
    tool_key: str
    args: dict = field(default_factory=dict)
    depends_on: int | None = None


@dataclass
class Plan:
    title: str
    steps: list[StepSpec] = field(default_factory=list)
    source: str = "template"          # template | model
    clarification: str | None = None  # set => mission needs_user_input


# --------------------------------------------------------------------------
# deterministic templates
# --------------------------------------------------------------------------

def _clean_goal(goal: str) -> str:
    return re.sub(r"\s+", " ", goal.strip())


def _plan_organize(goal: str) -> Plan:
    folder = "downloads"
    match = re.search(r"\b(?:folder|directory|dir)\s+([A-Za-z0-9_/\-.]+)", goal, re.I)
    if match:
        folder = match.group(1).strip("/")
    return Plan(
        title=f"Organize {folder}",
        steps=[
            StepSpec(1, f"Inspect {folder}", "fs.list", {"path": folder}),
            StepSpec(2, "Organize files by type", "fs.organize", {"source": folder}, depends_on=1),
            StepSpec(3, "Verify organization", "fs.list", {"path": folder}, depends_on=2),
        ],
    )


def _plan_research(goal: str) -> Plan:
    topic = re.sub(r"^(please\s+)?(research|investigate|look\s*up|study|find\s+out\s+about)\s+(about\s+)?",
                   "", goal, flags=re.I)
    topic = re.sub(r"\s+and\s+(prepare|create|write|save)\b.*$", "", topic, flags=re.I)
    topic = _clean_goal(topic) or goal
    return Plan(
        title=f"Research: {topic[:60]}",
        steps=[
            StepSpec(1, f"Search the web for '{topic}'", "web.search", {"query": topic}),
            StepSpec(
                2,
                "Save research notes",
                "notes.create",
                {
                    "title": f"Research: {topic[:60]}",
                    "content": f"Topic: {topic}\n\nCollected search results:\n{{{{prev_json}}}}\n\n"
                               "Note: offline search may be unavailable — check verification before citing.",
                },
                depends_on=1,
            ),
            StepSpec(3, "Verify notes exist", "notes.search", {"query": topic[:40]}, depends_on=2),
        ],
    )


def _plan_reminder(goal: str) -> Plan:
    when_match = TIME_EXPR.search(goal)
    if not when_match:
        return Plan(
            title="Reminder (needs a time)",
            clarification="When should I remind you? Try an offset like '+2h', 'tomorrow', "
                          "or an ISO time like 2025-01-31T09:00.",
        )
    message = re.sub(r"^(please\s+)?remind\s+me\s+(to\s+|about\s+|that\s+)?", "", goal, flags=re.I)
    message = _clean_goal(message) or goal
    return Plan(
        title=f"Reminder: {message[:50]}",
        steps=[StepSpec(1, f"Schedule reminder for '{when_match.group(1)}'", "reminder.create",
                        {"message": message, "when": when_match.group(1)})],
    )


def _plan_note(goal: str) -> Plan:
    content = re.sub(r"^(please\s+)?(write|create|make|take)\s+(a|an)\s+note\s+(about|that|saying)?\s*",
                     "", goal, flags=re.I)
    content = _clean_goal(content) or goal
    return Plan(
        title=f"Note: {content[:50]}",
        steps=[StepSpec(1, "Create the note", "notes.create",
                        {"title": f"Note: {content[:60]}", "content": content})],
    )


def _plan_delete(goal: str) -> Plan:
    quoted = re.search(r"['\"](.+?)['\"]", goal)
    path = quoted.group(1) if quoted else re.sub(r"^(please\s+)?delete\s+(the\s+)?(file\s+)?", "", goal, flags=re.I)
    path = _clean_goal(path)
    return Plan(
        title=f"Delete {path}",
        steps=[StepSpec(1, f"Move '{path}' to trash (needs approval)", "fs.delete", {"path": path})],
    )


def _plan_status(goal: str) -> Plan:
    return Plan(title="System status check",
                steps=[StepSpec(1, "Collect system status", "system.status", {})])


def _plan_generic(goal: str) -> Plan:
    return Plan(
        title=goal[:60] or "Mission",
        steps=[
            StepSpec(1, "Collect system context", "system.status", {}),
            StepSpec(
                2,
                "Record the objective as a tracked note",
                "notes.create",
                {
                    "title": f"Goal: {goal[:60]}",
                    "content": f"Objective: {goal}\n\nRecorded by JARVIS X. No matching specialized "
                               "workflow yet — configure a model provider for richer planning.",
                },
                depends_on=1,
            ),
        ],
    )


_TEMPLATE_MATCHERS: list[tuple[str, re.Pattern[str], object]] = [
    ("organize", re.compile(r"\b(organize|organi[sz]e|clean\s*up|sort|tidy)\b", re.I), _plan_organize),
    ("reminder", re.compile(r"\bremind\b", re.I), _plan_reminder),
    ("delete", re.compile(r"\b(delete|remove)\b", re.I), _plan_delete),
    ("research", re.compile(r"\b(research|investigate|look\s*up|study)\b", re.I), _plan_research),
    ("note", re.compile(r"\bnote\b", re.I), _plan_note),
    ("status", re.compile(r"\b(status|health\s*check|system\s*report)\b", re.I), _plan_status),
]


def _template_plan(goal: str) -> Plan:
    for _name, pattern, builder in _TEMPLATE_MATCHERS:
        if pattern.search(goal):
            return builder(goal)
    return _plan_generic(goal)


# --------------------------------------------------------------------------
# model-assisted planning
# --------------------------------------------------------------------------

def _plan_with_model(goal: str) -> Plan | None:
    if config.settings.provider_mode == "offline" or not model_router.remote.available():
        return None
    catalog = "\n".join(
        f"- {m['key']} (tier {m['risk_tier']}): {m['description']} | args: "
        f"{json.dumps((m['schema'] or {}).get('properties', {}))}"
        for m in registry.metas()
    )
    system = (
        "You are the JARVIS X mission planner. Decompose the user's goal into steps that use ONLY "
        "the registered tools listed below. Respond with STRICT JSON, no prose:\n"
        '{"title": string, "steps": [{"seq": 1, "title": string, "tool": tool_key, "args": object}]}\n'
        "Rules: max 6 steps; a verification step must come last; use tier-3 tools only when the goal "
        "requires them; never invent tools.\n\nRegistered tools:\n" + catalog
    )
    try:
        reply = model_router.chat(
            [ChatMessage(role="system", content=system), ChatMessage(role="user", content=goal)],
            temperature=0.1,
            max_tokens=900,
        )
    except ProviderError as exc:
        log.warning("model planning unavailable: %s", exc)
        return None

    match = re.search(r"\{.*\}", reply.text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        raw_steps = data["steps"]
        if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= config.settings.max_mission_steps):
            return None
        steps: list[StepSpec] = []
        for index, raw in enumerate(raw_steps[: config.settings.max_mission_steps], start=1):
            tool = registry.require(str(raw["tool"]))  # raises ToolError on unknown tool
            args = registry.validate_args(tool, dict(raw.get("args") or {}))
            steps.append(
                StepSpec(
                    seq=index,
                    title=str(raw.get("title") or tool.name)[:120],
                    tool_key=tool.key,
                    args=args,
                    depends_on=index - 1 if index > 1 else None,
                )
            )
        title = str(data.get("title") or goal)[:80]
        return Plan(title=title, steps=steps, source="model")
    except (KeyError, TypeError, ValueError, ToolError) as exc:
        log.warning("model plan rejected: %s", exc)
        return None


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def create_mission(goal: str, *, user_id: int | None = None, actor: str = "api") -> dict:
    goal = _clean_goal(goal)
    if not goal:
        raise ValueError("goal must not be empty")
    register_builtin_tools()

    plan = _plan_with_model(goal) or _template_plan(goal)
    status = "needs_user_input" if plan.clarification else "planned"
    now = db.now_iso()
    mission_id = db.execute(
        "INSERT INTO missions (user_id, title, goal, status, risk_level, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'low', ?, ?)",
        (user_id or db.default_user_id(), plan.title, goal, status, now, now),
    )
    if plan.clarification:
        db.execute(
            "INSERT INTO task_events (mission_id, event, detail_json, created_at) VALUES (?, ?, ?, ?)",
            (mission_id, "needs_clarification", db.dumps({"question": plan.clarification}), now),
        )
    for step in plan.steps:
        db.execute(
            "INSERT INTO mission_steps (mission_id, seq, title, tool_key, args_json, depends_on, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (mission_id, step.seq, step.title, step.tool_key, db.dumps(step.args), step.depends_on),
        )
    audit.log_event(
        actor,
        "mission.created",
        subject=f"mission:{mission_id}",
        detail={"title": plan.title, "source": plan.source, "steps": len(plan.steps),
                "status": status},
    )
    return {
        "mission": db.one("SELECT * FROM missions WHERE id = ?", (mission_id,)),
        "steps": db.query("SELECT * FROM mission_steps WHERE mission_id = ? ORDER BY seq", (mission_id,)),
        "clarification": plan.clarification,
        "plan_source": plan.source,
    }
