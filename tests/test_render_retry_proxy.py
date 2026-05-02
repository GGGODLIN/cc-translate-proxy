"""Tests for render UI's /api/session/{session}/turns/{turn_id}/retry proxy."""
import httpx
import pytest
import respx
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
api_key_env = "K"
model = "m"
display_name = "A"
''')
    monkeypatch.setenv("K", "v")
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))
    monkeypatch.setenv("CC_I18N_PROXY_AUDIT_DIR", str(home / "audit"))
    import importlib

    import scripts.render_server as rs
    importlib.reload(rs)
    yield TestClient(rs.app), home
    importlib.reload(rs)


def test_retry_proxy_forwards_to_proxy_daemon(render_app):
    client, _ = render_app
    with respx.mock(base_url="http://localhost:8080") as router:
        router.post("/v1/internal/retry").mock(
            return_value=httpx.Response(200, json={
                "turn_id": 999, "retry_of": 1,
                "translation_status": {"assistant": "ok"},
                "translation_providers": {"assistant": "a"},
                "assistant_zh": "重試成功",
                "failover_attempts": {}, "failover_errors": {},
            })
        )
        resp = client.post("/api/session/sess1/turns/1/retry", json={"head": "a"})
    assert resp.status_code == 200
    assert resp.json()["retry_of"] == 1


def test_retry_proxy_forwards_404(render_app):
    client, _ = render_app
    with respx.mock(base_url="http://localhost:8080") as router:
        router.post("/v1/internal/retry").mock(
            return_value=httpx.Response(404, json={"detail": "turn not found"})
        )
        resp = client.post("/api/session/sess1/turns/1/retry", json={})
    assert resp.status_code == 404
