"""Tests for /api/session/{s}/recap/latest endpoint (Tier (f))."""
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def render_app(tmp_path, monkeypatch):
    home = tmp_path / "cc-i18n-proxy"
    home.mkdir()
    audit = tmp_path / "audit"
    audit.mkdir()
    (home / "providers.toml").write_text('''
default_chain = ["a"]
[providers.a]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A"
model = "model-a"
display_name = "Provider A"
''', encoding="utf-8")
    monkeypatch.setenv("KEY_A", "test")
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))
    monkeypatch.setenv("CC_I18N_PROXY_AUDIT_DIR", str(audit))

    import scripts.render_server as rs
    importlib.reload(rs)
    yield TestClient(rs.app), audit
    importlib.reload(rs)


def _w(p: Path, entries: list[dict]) -> None:
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                 encoding="utf-8")


def test_recap_endpoint_404_when_session_missing(render_app):
    client, _ = render_app
    resp = client.get("/api/session/does-not-exist/recap/latest")
    assert resp.status_code == 404


def test_recap_endpoint_404_when_no_recap_turns(render_app):
    client, audit = render_app
    sid = "norecap"
    _w(audit / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "hi", "assistant_zh": "hello",
         "prompt_source": "human"},
    ])
    resp = client.get(f"/api/session/{sid}/recap/latest")
    assert resp.status_code == 404


def test_recap_endpoint_returns_recap_content(render_app):
    client, audit = render_app
    sid = "hasrecap"
    _w(audit / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "hi", "assistant_zh": "hello",
         "prompt_source": "human"},
        {"turn_id": 2, "timestamp": "2026-05-02T10:00:00Z",
         "user_zh": "The user stepped away and is coming back.",
         "assistant_zh": "您正在做某件重要的事",
         "prompt_source": "recap"},
    ])
    resp = client.get(f"/api/session/{sid}/recap/latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["assistant_zh"] == "您正在做某件重要的事"
    assert data["timestamp"] == "2026-05-02T10:00:00Z"
    assert data["turn_id"] == 2


def test_recap_endpoint_returns_latest_when_multiple(render_app):
    client, audit = render_app
    sid = "multi"
    _w(audit / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "The user stepped away.",
         "assistant_zh": "舊 recap", "prompt_source": "recap"},
        {"turn_id": 2, "user_zh": "human", "assistant_zh": "回",
         "prompt_source": "human"},
        {"turn_id": 3, "user_zh": "The user is coming back.",
         "assistant_zh": "最新 recap", "prompt_source": "recap"},
    ])
    resp = client.get(f"/api/session/{sid}/recap/latest")
    assert resp.status_code == 200
    assert resp.json()["assistant_zh"] == "最新 recap"


def test_recap_endpoint_works_for_legacy_audit_entries(render_app):
    """Legacy audit (no prompt_source) classified inline → still works."""
    client, audit = render_app
    sid = "legacy"
    _w(audit / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "human prompt", "assistant_zh": "回應"},
        {"turn_id": 2,
         "user_zh": "The user stepped away and is coming back. Recap in under 40 words.",
         "assistant_zh": "legacy recap content"},
    ])
    resp = client.get(f"/api/session/{sid}/recap/latest")
    assert resp.status_code == 200
    assert resp.json()["assistant_zh"] == "legacy recap content"


def test_recap_endpoint_invalid_session_id(render_app):
    client, _ = render_app
    resp = client.get("/api/session/..%2F..%2Fetc/recap/latest")
    # FastAPI URL-decodes; we get path traversal attempt → 400 or 404 acceptable
    assert resp.status_code in (400, 404)
