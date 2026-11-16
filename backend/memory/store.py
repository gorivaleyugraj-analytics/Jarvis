"""Memory subsystem (spec §7.2).

Conversation memory (recent dialogue) is read by the chat pipeline; long-term
memories are explicit, user-controlled facts/notes with inspect, correct,
export and delete controls (§7.2.14). Nothing is stored automatically —
memories are only written by tools or direct user action.
"""
from __future__ import annotations

from ..core import db

VALID_KINDS = ("fact", "preference", "note", "procedure")


def add(content: str, *, kind: str = "fact", source: str = "user", user_id: int | None = None) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("memory content must not be empty")
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    user_id = user_id or db.default_user_id()
    now = db.now_iso()
    mid = db.execute(
        "INSERT INTO memories (user_id, kind, content, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, kind, content, source, now, now),
    )
    return get(mid)


def get(memory_id: int) -> dict | None:
    return db.one("SELECT * FROM memories WHERE id = ?", (memory_id,))


def search(query: str = "", *, kind: str | None = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM memories WHERE 1=1"
    params: list = []
    if query.strip():
        sql += " AND content LIKE ?"
        params.append(f"%{query.strip()}%")
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    return db.query(sql, params)


def relevant_for_context(query: str, *, limit: int = 3) -> list[str]:
    """Small, cheap context selection for the chat pipeline (§7.1.7)."""
    rows = search(query, limit=limit)
    return [r["content"] for r in rows]


def update(memory_id: int, content: str, *, kind: str | None = None) -> dict | None:
    existing = get(memory_id)
    if not existing:
        return None
    new_kind = kind or existing["kind"]
    if new_kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    db.execute(
        "UPDATE memories SET content = ?, kind = ?, updated_at = ? WHERE id = ?",
        (content.strip(), new_kind, db.now_iso(), memory_id),
    )
    return get(memory_id)


def delete(memory_id: int) -> bool:
    if not get(memory_id):
        return False
    db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return True


def delete_all() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM memories")
    db.execute("DELETE FROM memories")
    return int(row["n"]) if row else 0


def export_all() -> list[dict]:
    """Full export for user-controlled data portability (§7.2.14)."""
    return db.query("SELECT * FROM memories ORDER BY created_at ASC")


def recent_messages(conversation_id: int, limit: int = 10) -> list[dict]:
    """Conversation memory: the most recent turns, oldest first."""
    rows = db.query(
        "SELECT role, content, created_at FROM messages WHERE conversation_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (conversation_id, max(1, limit)),
    )
    return list(reversed(rows))
