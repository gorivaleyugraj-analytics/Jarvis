"""Memory controls: add, search, correct, delete, export (spec §7.2.14)."""

from .conftest import H


def test_add_and_search(client):
    client.post("/api/v1/memory", json={"content": "I prefer dark mode", "kind": "preference"}, headers=H)
    client.post("/api/v1/memory", json={"content": "Campus360 deadline is Friday", "kind": "fact"}, headers=H)

    hits = client.get("/api/v1/memory?q=dark", headers=H).json()
    assert len(hits) == 1
    assert hits[0]["kind"] == "preference"

    everything = client.get("/api/v1/memory", headers=H).json()
    assert len(everything) == 2


def test_invalid_kind_rejected(client):
    res = client.post("/api/v1/memory", json={"content": "x", "kind": "nope"}, headers=H)
    assert res.status_code == 400


def test_correct_and_delete_single(client):
    memory_id = client.post(
        "/api/v1/memory", json={"content": "wrong fact"}, headers=H
    ).json()["id"]

    updated = client.patch(
        f"/api/v1/memory/{memory_id}", json={"content": "corrected fact"}, headers=H
    ).json()
    assert updated["content"] == "corrected fact"

    assert client.delete(f"/api/v1/memory/{memory_id}", headers=H).status_code == 200
    assert client.get("/api/v1/memory", headers=H).json() == []
    assert client.delete(f"/api/v1/memory/{memory_id}", headers=H).status_code == 404


def test_export_and_wipe(client):
    for text in ("fact one", "fact two"):
        client.post("/api/v1/memory", json={"content": text}, headers=H)

    exported = client.get("/api/v1/memory/export", headers=H).json()
    assert len(exported["items"]) == 2
    assert exported["exported_at"]

    wiped = client.delete("/api/v1/memory", headers=H).json()
    assert wiped["deleted"] == 2
    assert client.get("/api/v1/memory/export", headers=H).json()["items"] == []


def test_memory_deletion_is_audited(client):
    memory_id = client.post("/api/v1/memory", json={"content": "temporary"}, headers=H).json()["id"]
    client.delete(f"/api/v1/memory/{memory_id}", headers=H)

    actions = [row["action"] for row in client.get("/api/v1/audit", headers=H).json()]
    assert "memory.created" in actions
    assert "memory.deleted" in actions
