"""Mission lifecycle: planning, verification, approvals, bounded retries."""

from backend.core import config, db

from .conftest import H


def _write(client, path, content="x"):
    res = client.post(
        "/api/v1/tools/fs.write/invoke",
        json={"args": {"path": path, "content": content}},
        headers=H,
    )
    assert res.json()["result"]["success"] is True


def _pending_for(client, mission_id):
    return [
        a for a in client.get("/api/v1/approvals", headers=H).json()["pending"]
        if a["mission_id"] == mission_id
    ]


def test_organize_mission_completes_with_verification(client):
    _write(client, "downloads/report.pdf", "doc")
    _write(client, "downloads/photo.jpg", "img")

    res = client.post(
        "/api/v1/missions",
        json={"goal": "organize my downloads folder"},
        headers=H,
    )
    body = res.json()
    assert body["mission"]["status"] == "completed"
    assert all(step["status"] == "completed" for step in body["steps"])
    assert all(step["verification"] for step in body["steps"]), "every step needs evidence"

    workspace = config.settings.workspace_dir
    assert (workspace / "downloads" / "documents" / "report.pdf").exists()
    assert (workspace / "downloads" / "images" / "photo.jpg").exists()


def test_delete_mission_denial_never_executes(client):
    _write(client, "downloads/keepme.txt")
    res = client.post(
        "/api/v1/missions",
        json={"goal": "delete the file 'downloads/keepme.txt'"},
        headers=H,
    ).json()
    assert res["mission"]["status"] == "awaiting_approval"

    pending = _pending_for(client, res["mission"]["id"])
    assert len(pending) == 1
    client.post(f"/api/v1/approvals/{pending[0]['id']}/decide", json={"approved": False}, headers=H)

    assert (config.settings.workspace_dir / "downloads" / "keepme.txt").exists()
    mission = db.one("SELECT status FROM missions WHERE id = ?", (res["mission"]["id"],))
    assert mission["status"] == "cancelled"


def test_delete_mission_approval_executes_and_verifies(client):
    _write(client, "downloads/gone.txt")
    res = client.post(
        "/api/v1/missions",
        json={"goal": "delete the file 'downloads/gone.txt'"},
        headers=H,
    ).json()
    mission_id = res["mission"]["id"]

    decide = client.post(
        f"/api/v1/approvals/{_pending_for(client, mission_id)[0]['id']}/decide",
        json={"approved": True},
        headers=H,
    ).json()
    report = decide["mission_report"]
    assert report["mission"]["status"] == "completed"
    assert not (config.settings.workspace_dir / "downloads" / "gone.txt").exists()
    assert list((config.settings.workspace_dir / ".trash").glob("gone.txt*"))


def test_reminder_without_time_asks_instead_of_guessing(client):
    res = client.post(
        "/api/v1/missions",
        json={"goal": "remind me to submit the assignment"},
        headers=H,
    ).json()
    assert res["mission"]["status"] == "needs_user_input"
    assert res["steps"] == []


def test_reminder_with_offset_schedules_durable_job(client):
    res = client.post(
        "/api/v1/missions",
        json={"goal": "remind me to call mom +2h"},
        headers=H,
    ).json()
    assert res["mission"]["status"] == "completed"
    job = db.one("SELECT * FROM scheduled_jobs WHERE kind = 'reminder'")
    assert job is not None and job["status"] == "scheduled"


def test_note_mission_completes(client):
    res = client.post(
        "/api/v1/missions",
        json={"goal": "write a note about the campus project deadline"},
        headers=H,
    ).json()
    assert res["mission"]["status"] == "completed"
    notes = list((config.settings.workspace_dir / "notes").glob("*.md"))
    assert notes, "note file must exist"


def test_unknown_tool_fails_mission(client):
    mission_id = db.execute(
        "INSERT INTO missions (user_id, title, goal, status, created_at, updated_at) "
        "VALUES (1, 't', 'g', 'planned', ?, ?)",
        (db.now_iso(), db.now_iso()),
    )
    db.execute(
        "INSERT INTO mission_steps (mission_id, seq, title, tool_key, args_json, status) "
        "VALUES (?, 1, 'step', 'nope.tool', '{}', 'pending')",
        (mission_id,),
    )
    res = client.post(f"/api/v1/missions/{mission_id}/run", headers=H).json()
    assert res["mission"]["status"] == "failed"
    assert "unknown tool" in res["stopped_reason"]


def test_retry_budget_is_bounded(client):
    mission_id = db.execute(
        "INSERT INTO missions (user_id, title, goal, status, created_at, updated_at) "
        "VALUES (1, 't', 'g', 'planned', ?, ?)",
        (db.now_iso(), db.now_iso()),
    )
    db.execute(
        "INSERT INTO mission_steps (mission_id, seq, title, tool_key, args_json, status) "
        "VALUES (?, 1, 'step', 'fs.read', '{\"path\": \"missing.txt\"}', 'pending')",
        (mission_id,),
    )
    for _ in range(config.settings.max_tool_retries):
        client.post(f"/api/v1/missions/{mission_id}/run", headers=H)
    attempts = db.one(
        "SELECT attempts FROM mission_steps WHERE mission_id = ?", (mission_id,)
    )["attempts"]
    assert attempts == config.settings.max_tool_retries

    # One more run must refuse to execute: the retry budget is exhausted.
    res = client.post(f"/api/v1/missions/{mission_id}/run", headers=H).json()
    assert res["stopped_reason"] == "retry budget exhausted"
    attempts_after = db.one(
        "SELECT attempts FROM mission_steps WHERE mission_id = ?", (mission_id,)
    )["attempts"]
    assert attempts_after == attempts, "no execution beyond the retry budget"


def test_emergency_stop_cancels_everything(client):
    res = client.post(
        "/api/v1/missions",
        json={"goal": "delete the file 'downloads/estop.txt'"},
        headers=H,
    ).json()
    assert res["mission"]["status"] == "awaiting_approval"

    out = client.post("/api/v1/emergency-stop", headers=H).json()
    assert out["missions_cancelled"] >= 1
    mission = db.one("SELECT status FROM missions WHERE id = ?", (res["mission"]["id"],))
    assert mission["status"] == "cancelled"
    pending = client.get("/api/v1/approvals", headers=H).json()["pending"]
    assert pending == []
