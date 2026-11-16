"""Health, authentication and chat persistence (spec §20 acceptance items)."""

H = {"Authorization": "Bearer dev-token"}


def test_health_is_public_and_ok(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["provider"]["mode"] == "offline"


def test_protected_routes_require_auth(client):
    assert client.get("/api/v1/usage").status_code == 401
    assert client.get("/api/v1/usage", headers=H).status_code == 200
    assert client.get("/api/v1/agents").status_code == 401


def test_wrong_token_rejected(client):
    res = client.get("/api/v1/usage", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_chat_persists_conversation(client):
    res = client.post("/api/v1/chat", json={"message": "hello jarvis"}, headers=H)
    assert res.status_code == 200
    body = res.json()
    assert body["provider"] == "offline"
    assert body["reply"]

    conversations = client.get("/api/v1/conversations", headers=H).json()
    assert len(conversations) == 1
    messages = client.get(
        f"/api/v1/conversations/{body['conversation_id']}/messages", headers=H
    ).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_chat_rejects_empty_message(client):
    res = client.post("/api/v1/chat", json={"message": "   "}, headers=H)
    assert res.status_code == 400


def test_offline_provider_is_honest_about_mode(client):
    body = client.post("/api/v1/chat", json={"message": "help"}, headers=H).json()
    assert "offline" in body["reply"].lower()
    assert body["degraded"] is True


def test_privacy_local_flag_forces_offline_provider(client):
    body = client.post(
        "/api/v1/chat",
        json={"message": "status", "privacy": "local"},
        headers=H,
    ).json()
    assert body["provider"] == "offline"
