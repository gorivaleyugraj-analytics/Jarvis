"""Tool foundation (spec §9, §16).

Every tool returns a structured ToolResult — never free-form text only —
carrying success/failure, a safe summary, the output, error details and
verification evidence. The orchestrator uses that evidence to distinguish
"attempted" from "verified".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Permission tiers (spec §15)
TIER_READ = 1        # read and analyze
TIER_REVERSIBLE = 2  # reversible, low-risk actions
TIER_SENSITIVE = 3   # sensitive or consequential actions

TIER_NAMES = {TIER_READ: "read", TIER_REVERSIBLE: "reversible", TIER_SENSITIVE: "sensitive"}

MAX_OUTPUT_CHARS = 20_000


class ToolError(Exception):
    """Raised by tools on invalid input or execution failure."""


@dataclass
class ToolResult:
    success: bool
    action: str
    summary: str
    output: Any = None
    error: str | None = None
    verification: dict | None = None
    approval_required: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "action": self.action,
            "summary": self.summary,
            "output": self.output,
            "error": self.error,
            "verification": self.verification,
            "approval_required": self.approval_required,
        }


@dataclass
class ToolContext:
    user_id: int
    actor: str = "api"
    mission_id: int | None = None
    step_id: int | None = None
    # Set only when the executor is re-running an action whose approval the
    # user explicitly granted (approval gateway, spec §7.7.58).
    approved_request_id: int | None = None
    dry_run: bool = False
    extra: dict = field(default_factory=dict)


class Tool:
    """Base class for all registered tools."""

    key: str = ""
    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    risk_tier: int = TIER_READ
    timeout_s: int = 30
    # JSON-schema style descriptor used for validation and the dashboard.
    schema: dict = {"type": "object", "properties": {}, "required": []}

    def run(self, args: dict, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        raise NotImplementedError

    def meta(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "risk_tier": self.risk_tier,
            "risk_label": TIER_NAMES.get(self.risk_tier, "unknown"),
            "timeout_s": self.timeout_s,
            "schema": self.schema,
        }


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated, {len(text) - limit} more characters]"
