-- JARVIS X database schema (spec §13). SQLite, WAL mode.
-- Ids are autoincrement integers; timestamps are ISO-8601 UTC strings.

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT 'android',
    status      TEXT NOT NULL DEFAULT 'registered',
    last_seen   TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    title       TEXT NOT NULL DEFAULT 'New conversation',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content          TEXT NOT NULL,
    meta_json        TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL DEFAULT 'fact' CHECK (kind IN ('fact', 'preference', 'note', 'procedure')),
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    tools_json  TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'ready'
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_key   TEXT NOT NULL,
    mission_id  INTEGER REFERENCES missions(id) ON DELETE CASCADE,
    step_id     INTEGER REFERENCES mission_steps(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'running',
    summary     TEXT NOT NULL DEFAULT '',
    started_at  TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS missions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    title          TEXT NOT NULL,
    goal           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'created'
                   CHECK (status IN ('created','clarified','planned','awaiting_approval',
                                     'running','verifying','completed','paused','failed',
                                     'cancelled','needs_user_input','partially_completed')),
    risk_level     TEXT NOT NULL DEFAULT 'low',
    result_summary TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    finished_at    TEXT
);

CREATE TABLE IF NOT EXISTS mission_steps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id    INTEGER NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    title         TEXT NOT NULL,
    tool_key      TEXT NOT NULL,
    args_json     TEXT NOT NULL DEFAULT '{}',
    depends_on    INTEGER,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','awaiting_approval','running','completed',
                                    'failed','skipped','cancelled')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    result_json   TEXT,
    verification  TEXT,
    approval_id   INTEGER REFERENCES approval_requests(id),
    started_at    TEXT,
    finished_at   TEXT,
    UNIQUE (mission_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_steps_mission ON mission_steps(mission_id, seq);

CREATE TABLE IF NOT EXISTS task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  INTEGER REFERENCES missions(id) ON DELETE CASCADE,
    step_id     INTEGER REFERENCES mission_steps(id) ON DELETE CASCADE,
    event       TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_mission ON task_events(mission_id, id);

CREATE TABLE IF NOT EXISTS tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    version     TEXT NOT NULL DEFAULT '1.0.0',
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    risk_tier   INTEGER NOT NULL DEFAULT 1 CHECK (risk_tier IN (1, 2, 3)),
    schema_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    version     TEXT NOT NULL DEFAULT '1.0.0',
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    trust       TEXT NOT NULL DEFAULT 'builtin' CHECK (trust IN ('builtin', 'reviewed', 'untrusted')),
    enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS integrations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'disconnected',
    scopes_json TEXT NOT NULL DEFAULT '[]'
);

-- Metadata only: raw secrets live in an encrypted store, never here (spec §13).
CREATE TABLE IF NOT EXISTS credentials_metadata (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    integration_id INTEGER REFERENCES integrations(id) ON DELETE CASCADE,
    label          TEXT NOT NULL,
    hint           TEXT NOT NULL DEFAULT '',
    stored_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    subject  TEXT NOT NULL,
    resource TEXT NOT NULL,
    scope    TEXT NOT NULL,
    granted  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (subject, resource, scope)
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  INTEGER REFERENCES missions(id) ON DELETE SET NULL,
    step_id     INTEGER REFERENCES mission_steps(id) ON DELETE SET NULL,
    tool_key    TEXT NOT NULL,
    action      TEXT NOT NULL,
    args_json   TEXT NOT NULL DEFAULT '{}',
    risk_tier   INTEGER NOT NULL DEFAULT 3,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','denied','expired','cancelled')),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    decided_at  TEXT,
    decided_by  TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    outcome     TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts DESC);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    run_at      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'scheduled'
                CHECK (status IN ('scheduled','running','done','failed','cancelled')),
    created_at  TEXT NOT NULL,
    last_result TEXT
);

CREATE TABLE IF NOT EXISTS model_usage (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT NOT NULL,
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0,
    request_id       TEXT
);

CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
