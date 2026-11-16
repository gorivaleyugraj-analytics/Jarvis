"""Permission and risk engine (spec §15).

Tier 1 (read/analyze) runs autonomously. Tier 2 (reversible, low-risk) runs
autonomously while JARVIS_AUTONOMOUS_TIER2 is enabled. Tier 3 (sensitive or
consequential) is never executed without an explicit, approved approval
request — the executor must pass its approved_request_id in the context.

Also owns the workspace boundary: every filesystem path a tool touches must
resolve inside the sandboxed workspace directory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core import config
from ..tools.base import TIER_READ, TIER_REVERSIBLE, TIER_SENSITIVE, Tool, ToolContext


class PolicyError(Exception):
    """Raised when a request violates policy (e.g. path escape)."""


@dataclass
class Decision:
    allowed: bool
    requires_approval: bool
    tier: int
    reason: str


def decide(tool: Tool, args: dict, ctx: ToolContext) -> Decision:
    tier = tool.risk_tier

    if ctx.approved_request_id:
        return Decision(
            allowed=True,
            requires_approval=False,
            tier=tier,
            reason=f"approved by user (request #{ctx.approved_request_id})",
        )

    if tier == TIER_READ:
        return Decision(True, False, tier, "read-only action, autonomous")

    if tier == TIER_REVERSIBLE:
        if config.settings.autonomous_tier2:
            return Decision(True, False, tier, "reversible low-risk action, autonomous")
        return Decision(False, True, tier, "autonomous Tier 2 disabled by configuration")

    if tier == TIER_SENSITIVE:
        return Decision(False, True, tier, "sensitive action requires explicit user approval")

    return Decision(False, True, tier, f"unknown risk tier {tier}")


def resolve_within_workspace(relative: str) -> Path:
    """Resolve `relative` inside the sandboxed workspace or raise PolicyError."""
    return resolve_within_root(config.settings.workspace_dir, relative)


def resolve_within_root(root: Path, relative: str) -> Path:
    root = root.resolve()
    raw = str(relative or "").strip()
    if raw.startswith("/") or raw.startswith("\\") or ":" in raw[:3]:
        raise PolicyError(f"absolute paths are not allowed inside the sandbox: {raw!r}")
    candidate = (root / raw).resolve() if raw else root
    if candidate != root and root not in candidate.parents:
        raise PolicyError(f"path escapes the sandboxed workspace: {raw!r}")
    return candidate


def describe_tiers() -> list[dict]:
    return [
        {"tier": TIER_READ, "label": "read", "policy": "runs autonomously"},
        {
            "tier": TIER_REVERSIBLE,
            "label": "reversible",
            "policy": "autonomous" if config.settings.autonomous_tier2 else "approval required",
        },
        {"tier": TIER_SENSITIVE, "label": "sensitive", "policy": "explicit approval required"},
    ]
