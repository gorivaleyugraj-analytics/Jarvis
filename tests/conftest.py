"""Pytest configuration: isolated temp storage and a fresh database per test.

Environment overrides are applied BEFORE any backend import (config.py reads
env at import time), then every test starts from a clean, re-seeded database.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="jarvis-tests-")
os.environ["JARVIS_DATA_DIR"] = str(Path(_TMP) / "data")
os.environ["JARVIS_WORKSPACE_DIR"] = str(Path(_TMP) / "workspace")
os.environ["JARVIS_PROVIDER"] = "offline"
os.environ["JARVIS_AUTH_TOKEN"] = "dev-token"
os.environ["JARVIS_WEB_SEARCH_ENABLED"] = ""

import pytest  # noqa: E402

H = {"Authorization": "Bearer dev-token"}

TABLES = [
    "users", "devices", "conversations", "messages", "memories", "projects",
    "agents", "agent_runs", "missions", "mission_steps", "task_events",
    "tools", "skills", "integrations", "credentials_metadata", "permissions",
    "approval_requests", "audit_logs", "scheduled_jobs", "model_usage",
    "system_settings",
]


@pytest.fixture(autouse=True)
def fresh_db():
    from backend.agents import registry as agents_registry
    from backend.core import db
    from backend.main import _seed_skills
    from backend.tools.registry import sync_to_db as sync_tools

    with db.db(commit=True) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM sqlite_sequence")  # reset AUTOINCREMENT counters
        conn.execute("PRAGMA foreign_keys=ON")
    db.init_db()
    agents_registry.sync_to_db()
    sync_tools()
    _seed_skills()
    yield


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app) as test_client:
        yield test_client
