"""Tests for the /api/last-enable polling endpoint on the render server."""
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cc_i18n_proxy.intl_sentinel import write_last_enable


@pytest.fixture
def render_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    home = tmp_path / "proxy_home"
    home.mkdir(parents=True, exist_ok=True)
    emit = tmp_path / "emit"
    emit.mkdir(parents=True, exist_ok=True)
    audit = home / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))
    monkeypatch.setenv("CC_I18N_PROXY_EMIT_DIR", str(emit))
    monkeypatch.setenv("CC_I18N_PROXY_AUDIT_DIR", str(audit))

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    if "render_server" in sys.modules:
        del sys.modules["render_server"]
    render_server = importlib.import_module("render_server")
    return TestClient(render_server.app)


def test_last_enable_returns_empty_when_workspace_has_no_sentinel(render_client):
    resp = render_client.get("/api/last-enable?workspace=default")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_last_enable_returns_payload_for_matching_workspace(render_client, tmp_path):
    home = tmp_path / "proxy_home"
    write_last_enable(home, workspace_id="default", session_id="abc123")
    resp = render_client.get("/api/last-enable?workspace=default")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "abc123"
    assert body["workspace_id"] == "default"
    assert body["ts"] > 0


def test_last_enable_workspace_isolation(render_client, tmp_path):
    """Sentinel written for ws-a must NOT leak into ws-b's response."""
    home = tmp_path / "proxy_home"
    write_last_enable(home, workspace_id="ws-a", session_id="aaa")
    resp_a = render_client.get("/api/last-enable?workspace=ws-a")
    resp_b = render_client.get("/api/last-enable?workspace=ws-b")
    assert resp_a.json()["session_id"] == "aaa"
    assert resp_b.json() == {}


def test_last_enable_rejects_invalid_workspace(render_client):
    resp = render_client.get("/api/last-enable?workspace=../escape")
    assert resp.status_code == 400


def test_last_enable_rejects_missing_workspace(render_client):
    resp = render_client.get("/api/last-enable")
    assert resp.status_code == 400
