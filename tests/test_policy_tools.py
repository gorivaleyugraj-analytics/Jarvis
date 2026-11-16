"""Policy tiers, workspace sandbox and the approval gateway (spec §15/§16)."""

import pathlib

from backend.core import config
from backend.tools.registry import register_builtin_tools, registry

from .conftest import H

register_builtin_tools()


def test_tier_classification():
    assert registry.get("fs.list").risk_tier == 1
    assert registry.get("fs.read").risk_tier == 1
    assert registry.get("fs.write").risk_tier == 2
    assert registry.get("fs.organize").risk_tier == 2
    assert registry.get("fs.delete").risk_tier == 3


def test_fs_write_read_roundtrip(client):
    res = client.post(
        "/api/v1/tools/fs.write/invoke",
        json={"args": {"path": "subdir/demo.txt", "content": "hello jarvis"}},
        headers=H,
    )
    assert res.json()["result"]["success"] is True

    res = client.post(
        "/api/v1/tools/fs.read/invoke",
        json={"args": {"path": "subdir/demo.txt"}},
        headers=H,
    )
    assert res.json()["result"]["output"]["content"] == "hello jarvis"


def test_unknown_tool_rejected(client):
    res = client.post("/api/v1/tools/nope.tool/invoke", json={"args": {}}, headers=H)
    assert res.status_code == 400


def test_unknown_argument_rejected(client):
    res = client.post(
        "/api/v1/tools/fs.write/invoke",
        json={"args": {"path": "x.txt", "content": "x", "evil_key": 1}},
        headers=H,
    )
    assert res.status_code == 400


def test_missing_required_argument_rejected(client):
    res = client.post("/api/v1/tools/fs.write/invoke", json={"args": {"path": "x.txt"}}, headers=H)
    assert res.status_code == 400


def test_sandbox_escape_rejected(client):
    for path in ("../escape.txt", "/etc/passwd", "a/../../b.txt"):
        res = client.post(
            "/api/v1/tools/fs.write/invoke",
            json={"args": {"path": path, "content": "x"}},
            headers=H,
        )
        assert res.status_code in (400, 403), path
        assert "sandbox" in res.json()["detail"]


def test_tier3_manual_invoke_requires_approval_and_deny_protects_file(client):
    client.post(
        "/api/v1/tools/fs.write/invoke",
        json={"args": {"path": "precious.txt", "content": "keep me"}},
        headers=H,
    )
    res = client.post(
        "/api/v1/tools/fs.delete/invoke",
        json={"args": {"path": "precious.txt"}},
        headers=H,
    )
    body = res.json()
    assert body["status"] == "approval_required"

    pending = client.get("/api/v1/approvals", headers=H).json()["pending"]
    assert len(pending) == 1 and pending[0]["tool_key"] == "fs.delete"

    decide = client.post(
        f"/api/v1/approvals/{pending[0]['id']}/decide",
        json={"approved": False},
        headers=H,
    )
    assert decide.status_code == 200
    workspace = config.settings.workspace_dir
    assert pathlib.Path(workspace / "precious.txt").exists(), "denied action must not execute"


def test_tier3_manual_invoke_executes_after_approval(client):
    client.post(
        "/api/v1/tools/fs.write/invoke",
        json={"args": {"path": "goner.txt", "content": "bye"}},
        headers=H,
    )
    res = client.post(
        "/api/v1/tools/fs.delete/invoke",
        json={"args": {"path": "goner.txt"}},
        headers=H,
    )
    approval_id = res.json()["approval"]["id"]

    decide = client.post(
        f"/api/v1/approvals/{approval_id}/decide",
        json={"approved": True},
        headers=H,
    ).json()
    assert decide["result"]["success"] is True

    workspace = config.settings.workspace_dir
    assert not pathlib.Path(workspace / "goner.txt").exists()
    assert list(pathlib.Path(workspace / ".trash").glob("goner.txt*")), "delete must be recoverable"
