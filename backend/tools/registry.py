"""Tool registry (spec §9): versioned tools with JSON-style schemas, risk
tiers and argument validation. Tools are registered explicitly — nothing is
discoverable or callable unless it was registered here.
"""
from __future__ import annotations

from .base import Tool, ToolError, ToolResult

_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.key:
            raise ToolError("tool must define a key")
        if tool.key in self._tools:
            raise ToolError(f"tool key already registered: {tool.key}")
        self._tools[tool.key] = tool

    def get(self, key: str) -> Tool | None:
        return self._tools.get(key)

    def require(self, key: str) -> Tool:
        tool = self._tools.get(key)
        if tool is None:
            raise ToolError(f"unknown tool: {key}")
        return tool

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def keys(self) -> list[str]:
        return list(self._tools.keys())

    def metas(self) -> list[dict]:
        return [t.meta() for t in self._tools.values()]

    # -- validation --------------------------------------------------------
    def validate_args(self, tool: Tool, args: dict) -> dict:
        if not isinstance(args, dict):
            raise ToolError("tool arguments must be an object")
        schema = tool.schema or {}
        properties = schema.get("properties", {}) or {}
        required = schema.get("required", []) or []

        for key in args:
            if key not in properties:
                raise ToolError(f"unknown argument '{key}' for tool {tool.key}")

        for key in required:
            if key not in args or args[key] is None or (isinstance(args[key], str) and not args[key].strip()):
                raise ToolError(f"missing required argument '{key}' for tool {tool.key}")

        for key, value in args.items():
            expected = properties.get(key, {}).get("type")
            if expected and not isinstance(value, _JSON_TYPES.get(expected, object)):
                raise ToolError(
                    f"argument '{key}' must be {expected}, got {type(value).__name__}"
                )
        return args


registry = ToolRegistry()


def register_builtin_tools() -> None:
    """Import and register every built-in tool module. Idempotent."""
    if registry.get("fs.list"):
        return
    from . import fs_tools, local_tools

    for tool in (*fs_tools.TOOLS, *local_tools.TOOLS):
        registry.register(tool)


def sync_to_db() -> None:
    """Mirror the registry into the tools table for the dashboard/audit."""
    for tool in registry.all():
        meta = tool.meta()
        db_exec(
            "INSERT INTO tools (key, version, name, description, risk_tier, schema_json, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version, name=excluded.name, "
            "description=excluded.description, risk_tier=excluded.risk_tier, "
            "schema_json=excluded.schema_json",
            (
                meta["key"],
                meta["version"],
                meta["name"],
                meta["description"],
                meta["risk_tier"],
                db_dumps(meta["schema"]),
            ),
        )


# Local aliases keep the import surface small and avoid a circular import with
# backend.core.db at module load time.
def db_exec(sql: str, params: tuple) -> int:
    from ..core import db

    return db.execute(sql, params)


def db_dumps(value) -> str:
    from ..core import db

    return db.dumps(value)
