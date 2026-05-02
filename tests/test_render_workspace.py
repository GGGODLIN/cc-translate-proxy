"""Tests for workspace-aware session listing on render UI."""
import json

import pytest


@pytest.fixture
def render_app(tmp_path, monkeypatch):
    home = tmp_path / "cc-i18n-proxy"
    home.mkdir()
    (home / "audit").mkdir()
    (home / "emit").mkdir()
    (home / "providers.toml").write_text(
        'default_chain = ["a"]\n[providers.a]\n'
        'kind="openai-compat"\nbase_url="u"\napi_key_env="K"\nmodel="m"\ndisplay_name="A"\n'
    )
    monkeypatch.setenv("K", "v")
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))
    monkeypatch.setenv("CC_I18N_PROXY_AUDIT_DIR", str(home / "audit"))
    monkeypatch.setenv("CC_I18N_PROXY_EMIT_DIR", str(home / "emit"))
    import importlib

    import scripts.render_server as rs
    importlib.reload(rs)
    yield rs, home
    importlib.reload(rs)


def test_read_session_workspace_returns_default_when_missing(render_app):
    rs, _home = render_app
    assert rs._read_session_workspace("missing") == ("default", "default")


def test_read_session_workspace_from_jsonl(render_app):
    rs, home = render_app
    sid = "session1aabb"
    (home / "audit" / f"{sid}.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": sid,
        "workspace_id": "W-X", "workspace_name": "My Workspace",
    }) + "\n")
    ws_id, ws_name = rs._read_session_workspace(sid)
    assert ws_id == "W-X"
    assert ws_name == "My Workspace"


def test_legacy_jsonl_returns_default(render_app):
    rs, home = render_app
    sid = "legacy12abcd"
    (home / "audit" / f"{sid}.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": sid,
    }) + "\n")
    ws_id, ws_name = rs._read_session_workspace(sid)
    assert ws_id == "default"
    assert ws_name == "default"


def test_list_sessions_by_workspace_groups_correctly(render_app):
    rs, home = render_app
    for sid, ws in [("a1aabb", "W1"), ("b2aabb", "W1"), ("c3aabb", "W2")]:
        (home / "audit" / f"{sid}.jsonl").write_text(json.dumps({
            "workspace_id": ws, "workspace_name": ws,
        }) + "\n")
        (home / "emit" / f"cc-i18n-{sid}.md").write_text("x")
    (home / "audit" / "legacyabcd.jsonl").write_text("{}\n")
    (home / "emit" / "cc-i18n-legacyabcd.md").write_text("x")

    by_ws = rs._list_sessions_by_workspace()
    assert set(by_ws.keys()) == {"W1", "W2", "default"}
    assert len(by_ws["W1"]) == 2
    assert len(by_ws["W2"]) == 1
    assert len(by_ws["default"]) == 1


def test_index_single_workspace_renders_flat(render_app):
    rs, home = render_app
    (home / "audit" / "abc1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "abc1aabb",
        "workspace_id": "default", "workspace_name": "default",
    }) + "\n")
    (home / "emit" / "cc-i18n-abc1aabb.md").write_text("# session abc")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "abc1aabb" in resp.text


def test_index_multi_workspace_renders_sections(render_app):
    rs, home = render_app
    (home / "audit" / "sess1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess1aabb",
        "workspace_id": "W1", "workspace_name": "Project A",
    }) + "\n")
    (home / "audit" / "sess2aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess2aabb",
        "workspace_id": "W2", "workspace_name": "Project B",
    }) + "\n")
    (home / "emit" / "cc-i18n-sess1aabb.md").write_text("# A")
    (home / "emit" / "cc-i18n-sess2aabb.md").write_text("# B")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Project A" in resp.text
    assert "Project B" in resp.text
    assert 'href="/W1"' in resp.text
    assert 'href="/W2"' in resp.text


def test_workspace_index_lists_workspace_sessions(render_app):
    rs, home = render_app
    (home / "audit" / "sess1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess1aabb",
        "workspace_id": "W1", "workspace_name": "Project A",
    }) + "\n")
    (home / "emit" / "cc-i18n-sess1aabb.md").write_text("# A")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/W1")
    assert resp.status_code == 200
    assert "Project A" in resp.text
    assert "sess1aabb" in resp.text


def test_workspace_session_detail_renders(render_app):
    rs, home = render_app
    (home / "audit" / "sess1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess1aabb",
        "workspace_id": "W1", "workspace_name": "Project A",
    }) + "\n")
    (home / "emit" / "cc-i18n-sess1aabb.md").write_text("# A")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/W1/sess1aabb")
    assert resp.status_code == 200
    assert "sess1aabb" in resp.text


def test_session_strip_appears_when_multiple_sessions_in_workspace(render_app):
    rs, home = render_app
    for sid in ("sess1aabb", "sess2aabb"):
        (home / "audit" / f"{sid}.jsonl").write_text(json.dumps({
            "turn_id": 1, "session_id": sid,
            "workspace_id": "W1", "workspace_name": "Project A",
        }) + "\n")
        (home / "emit" / f"cc-i18n-{sid}.md").write_text(f"# {sid}")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/W1/sess1aabb")
    assert 'class="session-tabs"' in resp.text
    assert 'class="session-tab active"' in resp.text
    n_tabs = (
        resp.text.count('class="session-tab"')
        + resp.text.count('class="session-tab active"')
    )
    assert n_tabs == 2


def test_legacy_session_url_still_works(render_app):
    rs, home = render_app
    (home / "audit" / "sess1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess1aabb",
        "workspace_id": "W1", "workspace_name": "Project A",
    }) + "\n")
    (home / "emit" / "cc-i18n-sess1aabb.md").write_text("# A")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/sess1aabb")
    assert resp.status_code == 200


def test_detail_page_includes_prompt_aware_nav_elements(render_app):
    """Tier (h): sticky prompt bars + scroll-to-bottom button injected in detail page."""
    rs, home = render_app
    (home / "audit" / "sess1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess1aabb",
        "workspace_id": "W1", "workspace_name": "Project A",
    }) + "\n")
    (home / "emit" / "cc-i18n-sess1aabb.md").write_text("# A")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/W1/sess1aabb")
    assert resp.status_code == 200
    body = resp.text
    assert 'id="sticky-prompt-top"' in body
    assert 'id="sticky-prompt-bottom"' in body
    assert 'id="scroll-bottom-btn"' in body
    assert "_scrollToStickyTarget" in body
    assert "_scrollToBottom" in body
    assert "_userPromptBlockquotes" in body


def test_detail_page_recap_btn_repositioned_above_scroll_bottom(render_app):
    """Tier (h): recap-btn pushed up to bottom:4em so scroll-to-bottom can occupy bottom:1.2em."""
    rs, home = render_app
    (home / "audit" / "sess1aabb.jsonl").write_text(json.dumps({
        "turn_id": 1, "session_id": "sess1aabb",
        "workspace_id": "W1", "workspace_name": "Project A",
    }) + "\n")
    (home / "emit" / "cc-i18n-sess1aabb.md").write_text("# A")
    from fastapi.testclient import TestClient
    client = TestClient(rs.app)
    resp = client.get("/W1/sess1aabb")
    body = resp.text
    assert ".recap-btn {\n  position: sticky; bottom: 4.6em; display: block;" in body
    assert ".recap-panel {\n  position: sticky; bottom: 7.6em; display: block;" in body
    assert ".scroll-bottom-btn {\n  position: sticky; bottom: 1.2em; display: block;" in body
