"""Tests for GET /api/session/{session}/turns endpoint."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def render_app(tmp_path, monkeypatch):
    home = tmp_path / "cc-i18n-proxy"
    home.mkdir()
    (home / "audit").mkdir()
    (home / "providers.toml").write_text('''
default_chain = ["a"]
[providers.a]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A"
model = "m"
display_name = "Provider Alpha"
[providers.b]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A"
model = "m"
display_name = "Provider Beta"
''')
    monkeypatch.setenv("KEY_A", "v")
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))
    monkeypatch.setenv("CC_I18N_PROXY_AUDIT_DIR", str(home / "audit"))
    import importlib

    import scripts.render_server as rs
    importlib.reload(rs)
    yield TestClient(rs.app), home
    importlib.reload(rs)


def _write_audit(home: Path, session: str, lines: list[dict]):
    p = home / "audit" / f"{session}.jsonl"
    p.write_text("\n".join(json.dumps(entry) for entry in lines) + "\n")


def test_returns_404_when_session_missing(render_app):
    client, _ = render_app
    resp = client.get("/api/session/missing/turns")
    assert resp.status_code == 404


def test_returns_per_turn_metadata(render_app):
    client, home = render_app
    _write_audit(home, "sess1", [
        {
            "turn_id": 1,
            "timestamp": "2026-05-01T10:00:00Z",
            "translation_providers": {"user": "a", "assistant": "a"},
            "failover_attempts": {"user": [], "assistant": []},
            "translation_status": {"user": "ok", "assistant": "ok"},
        },
        {
            "turn_id": 2,
            "timestamp": "2026-05-01T10:01:00Z",
            "translation_providers": {"user": "a", "assistant": "b"},
            "failover_attempts": {"user": [], "assistant": ["a"]},
            "translation_status": {"user": "ok", "assistant": "ok"},
        },
    ])
    resp = client.get("/api/session/sess1/turns")
    assert resp.status_code == 200
    turns = resp.json()
    assert len(turns) == 2
    assert turns[0]["translation_providers_display"]["user"] == "Provider Alpha"
    assert turns[1]["translation_providers_display"]["assistant"] == "Provider Beta"
    assert turns[1]["failover_attempts"]["assistant"] == ["a"]


def test_legacy_entries_marked(render_app):
    client, home = render_app
    _write_audit(home, "legacy", [
        {"turn_id": 1, "timestamp": "...", "translation_status": "ok"},
    ])
    resp = client.get("/api/session/legacy/turns")
    assert resp.status_code == 200
    turns = resp.json()
    assert turns[0]["translation_providers"] == {}
    assert turns[0]["translation_providers_display"] == {}


def test_path_traversal_rejected(render_app):
    client, _ = render_app
    resp = client.get("/api/session/..%2Fetc%2Fpasswd/turns")
    assert resp.status_code in (400, 404)
