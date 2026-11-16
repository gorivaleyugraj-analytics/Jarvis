"""JARVIS X backend entry point.

Boots the SQLite schema, registers built-in tools and agents, mounts the
versioned /api/v1 routers and serves the dashboard as a static SPA at "/".
Run from the project root:  uvicorn backend.main:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents import registry as agents_registry
from .api import routes_agents, routes_chat, routes_health, routes_memory, routes_missions
from .core import config, db
from .core.logging_setup import configure, get_logger
from .tools.base import ToolError
from .tools.registry import register_builtin_tools, sync_to_db as sync_tools_to_db

log = get_logger("main")

# Builtin skills are trusted; third-party integrations stay 'untrusted' until
# reviewed (spec §9). Planned entries document the roadmap without pretending
# to be implemented.
BUILTIN_SKILLS: list[dict] = [
    {"key": "notes-and-reminders", "version": "1.0.0", "name": "Notes & Reminders",
     "description": "Local notes, reminders and system status tools.", "trust": "builtin", "enabled": 1},
    {"key": "workspace-files", "version": "1.0.0", "name": "Workspace Files",
     "description": "Sandboxed file operations inside the JARVIS workspace.", "trust": "builtin", "enabled": 1},
    {"key": "web-research", "version": "1.0.0", "name": "Web Research",
     "description": "Web search with honest offline degradation.", "trust": "builtin", "enabled": 1},
    {"key": "android-control", "version": "0.0.1", "name": "Android Control",
     "description": "Planned (Phase 4): device operations via the Kotlin app bridge.",
     "trust": "untrusted", "enabled": 0},
    {"key": "browser-automation", "version": "0.0.1", "name": "Browser Automation",
     "description": "Planned (Phase 7): Playwright workflows on a supported host.",
     "trust": "untrusted", "enabled": 0},
    {"key": "gmail", "version": "0.0.1", "name": "Gmail Connector",
     "description": "Planned (Phase 7): authorized Gmail read/draft/send.",
     "trust": "untrusted", "enabled": 0},
    {"key": "calendar", "version": "0.0.1", "name": "Calendar Connector",
     "description": "Planned (Phase 7): authorized calendar management.",
     "trust": "untrusted", "enabled": 0},
    {"key": "github", "version": "0.0.1", "name": "GitHub Connector",
     "description": "Planned (Phase 7): repos, branches and prepared commits.",
     "trust": "untrusted", "enabled": 0},
]


def _seed_skills() -> None:
    for skill in BUILTIN_SKILLS:
        db.execute(
            "INSERT INTO skills (key, version, name, description, trust, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET version=excluded.version, name=excluded.name, "
            "description=excluded.description, trust=excluded.trust, enabled=excluded.enabled",
            (skill["key"], skill["version"], skill["name"], skill["description"],
             skill["trust"], skill["enabled"]),
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(
        "JARVIS X %s ready — provider=%s, workspace=%s",
        config.APP_VERSION,
        config.settings.provider_mode,
        config.settings.workspace_dir,
    )
    yield
    log.info("JARVIS X shutting down")


def create_app() -> FastAPI:
    configure(config.settings.log_level)
    db.init_db()
    register_builtin_tools()
    sync_tools_to_db()
    agents_registry.sync_to_db()
    _seed_skills()

    app = FastAPI(
        title=config.APP_NAME,
        version=config.APP_VERSION,
        description="Permission-aware personal AI assistant backend (spec §14 API).",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # dev convenience; tighten for deployment
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ValueError)
    async def _value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ToolError)
    async def _tool_error_handler(_: Request, exc: ToolError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    for router in (
        routes_health.router,
        routes_chat.router,
        routes_missions.router,
        routes_agents.router,
        routes_memory.router,
    ):
        app.include_router(router)

    dashboard_dir = config.settings.base_dir / "dashboard"
    if dashboard_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")
    return app


app = create_app()
