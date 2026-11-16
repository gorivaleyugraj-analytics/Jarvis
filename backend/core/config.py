"""JARVIS X core configuration.

Every setting is environment-driven (12-factor style). A minimal .env loader
is built in so the project runs with no extra dependencies. See .env.example
for the documented keys.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

APP_NAME = "JARVIS X"
APP_VERSION = "0.1.0"
API_PREFIX = "/api/v1"


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs without clobbering real environment variables."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _env_str(key: str, default: str) -> str:
    raw = os.getenv(key)
    return raw.strip() if raw and raw.strip() else default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Paths
    base_dir: Path
    data_dir: Path
    workspace_dir: Path
    db_path: Path

    # Security
    auth_token: str

    # Model provider
    provider_mode: str  # auto | offline | openai_compat
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    provider_timeout_s: int

    # Reliability limits
    max_mission_steps: int
    max_tool_retries: int
    step_timeout_s: int

    # Approvals & budget
    approval_ttl_minutes: int
    daily_cost_limit_usd: float

    # Policy
    autonomous_tier2: bool

    # Logging
    log_level: str


def _build_settings() -> Settings:
    base_dir = BASE_DIR
    data_dir = (base_dir / _env_str("JARVIS_DATA_DIR", "storage/data")).resolve()
    workspace_dir = (base_dir / _env_str("JARVIS_WORKSPACE_DIR", "storage/workspace")).resolve()
    s = Settings(
        base_dir=base_dir,
        data_dir=data_dir,
        workspace_dir=workspace_dir,
        db_path=data_dir / "jarvis.db",
        auth_token=_env_str("JARVIS_AUTH_TOKEN", "dev-token"),
        provider_mode=_env_str("JARVIS_PROVIDER", "auto").lower(),
        openai_api_key=_env_str("JARVIS_OPENAI_API_KEY", ""),
        openai_base_url=_env_str("JARVIS_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        openai_model=_env_str("JARVIS_OPENAI_MODEL", "gpt-4o-mini"),
        provider_timeout_s=_env_int("JARVIS_PROVIDER_TIMEOUT_S", 45),
        max_mission_steps=_env_int("JARVIS_MAX_MISSION_STEPS", 12),
        max_tool_retries=_env_int("JARVIS_MAX_TOOL_RETRIES", 3),
        step_timeout_s=_env_int("JARVIS_STEP_TIMEOUT_S", 120),
        approval_ttl_minutes=_env_int("JARVIS_APPROVAL_TTL_MINUTES", 30),
        daily_cost_limit_usd=_env_float("JARVIS_DAILY_COST_LIMIT_USD", 5.0),
        autonomous_tier2=_env_bool("JARVIS_AUTONOMOUS_TIER2", True),
        log_level=_env_str("JARVIS_LOG_LEVEL", "INFO").upper(),
    )
    # Storage layout: the sandboxed workspace every file tool is confined to.
    for directory in (
        s.data_dir,
        s.workspace_dir,
        s.workspace_dir / "downloads",
        s.workspace_dir / "notes",
        s.workspace_dir / "reports",
        s.workspace_dir / ".trash",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return s


settings = _build_settings()


def provider_configured() -> bool:
    """True when a real model provider is usable (key + non-offline mode)."""
    if settings.provider_mode == "offline":
        return False
    return bool(settings.openai_api_key)
