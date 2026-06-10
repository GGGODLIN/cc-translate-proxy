"""Test session tab label fallback chain (recap → human → sid) and top bar API."""
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.render_server import _session_tab_label


def _w(p: Path, entries: list[dict]) -> None:
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries),
                 encoding="utf-8")


def test_label_uses_first_human_user_zh(tmp_path):
    sid = "a3f9bd5e1e7c"
    _w(tmp_path / f"{sid}.jsonl", [{
        "timestamp": "t", "session_id": sid, "turn_id": 1,
        "user_zh": "幫我看 hostname 然後告訴我系統版本以及記憶體大小",
        "assistant_zh": "...",
        "prompt_source": "human",
    }])
    assert _session_tab_label(tmp_path, sid) == "幫我看 hostname 然後告訴我系統版本以及記憶體大小"[:40]


def test_label_skips_command_falls_to_first_human(tmp_path):
    sid = "skipcmd"
    _w(tmp_path / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "<command-message>intl</command-message>",
         "assistant_zh": "翻譯模式啟動", "prompt_source": "command"},
        {"turn_id": 2, "user_zh": "我想做什麼什麼",
         "assistant_zh": "好", "prompt_source": "human"},
    ])
    assert _session_tab_label(tmp_path, sid) == "我想做什麼什麼"


def test_label_prefers_latest_recap_assistant_zh(tmp_path):
    sid = "recapwins"
    _w(tmp_path / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "第一個真人 prompt",
         "assistant_zh": "我的回應", "prompt_source": "human"},
        {"turn_id": 2, "user_zh": "The user stepped away and is coming back. Recap...",
         "assistant_zh": "您正在測試 cc-i18n-proxy 的翻譯流程",
         "prompt_source": "recap"},
    ])
    assert _session_tab_label(tmp_path, sid) == "您正在測試 cc-i18n-proxy 的翻譯流程"[:40]


def test_label_uses_latest_recap_when_multiple(tmp_path):
    sid = "multrecap"
    _w(tmp_path / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "human one", "assistant_zh": "x",
         "prompt_source": "human"},
        {"turn_id": 2, "user_zh": "The user stepped away...",
         "assistant_zh": "舊的 recap", "prompt_source": "recap"},
        {"turn_id": 3, "user_zh": "human two", "assistant_zh": "y",
         "prompt_source": "human"},
        {"turn_id": 4, "user_zh": "The user stepped away again",
         "assistant_zh": "新的 recap", "prompt_source": "recap"},
    ])
    assert _session_tab_label(tmp_path, sid) == "新的 recap"


def test_label_legacy_entry_classified_inline(tmp_path):
    """Old audit JSONL has no prompt_source field → classifier infers from user_zh."""
    sid = "legacy"
    _w(tmp_path / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "<command-message>intl</command-message>",
         "assistant_zh": "翻譯模式啟動"},
        {"turn_id": 2, "user_zh": "The user stepped away and is coming back. Recap in under 40 words.",
         "assistant_zh": "您正在做某事", "translation_sources": {}},
    ])
    assert _session_tab_label(tmp_path, sid) == "您正在做某事"


def test_label_falls_back_to_sid_when_no_audit(tmp_path):
    assert _session_tab_label(tmp_path, "abc12345fff") == "abc12345…"


def test_label_falls_back_to_sid_when_audit_empty(tmp_path):
    sid = "emptysid"
    (tmp_path / f"{sid}.jsonl").write_text("", encoding="utf-8")
    assert _session_tab_label(tmp_path, sid) == "emptysid…"


def test_label_falls_back_to_sid_when_audit_malformed(tmp_path):
    sid = "badjson12"
    (tmp_path / f"{sid}.jsonl").write_text("not json\n", encoding="utf-8")
    assert _session_tab_label(tmp_path, sid) == "badjson1…"


def test_label_falls_back_to_sid_when_only_system_turns(tmp_path):
    """Session with only command/hook/recap turns and no human → sid fallback."""
    sid = "onlysys"
    _w(tmp_path / f"{sid}.jsonl", [
        {"turn_id": 1, "user_zh": "<command-message>intl</command-message>",
         "assistant_zh": "翻譯模式啟動", "prompt_source": "command"},
        {"turn_id": 2, "user_zh": "Stop hook feedback: ...",
         "assistant_zh": "跳過", "prompt_source": "hook"},
    ])
    # No recap, no human → sid fallback
    assert _session_tab_label(tmp_path, sid) == "onlysys…"


@pytest.fixture
def render_app(tmp_path, monkeypatch):
    home = tmp_path / "cc-i18n-proxy"
    home.mkdir()
    (home / "providers.toml").write_text('''
default_chain = ["a", "b"]

[providers.a]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A"
model = "model-a"
display_name = "Provider A"

[providers.b]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A"
model = "model-b"
display_name = "Provider B"

[providers.c]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A"
model = "model-c"
display_name = "Provider C"
enabled = false
''')
    monkeypatch.setenv("KEY_A", "test")
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))

    import scripts.render_server as rs
    importlib.reload(rs)
    yield TestClient(rs.app), home
    importlib.reload(rs)


def test_get_active_lists_enabled_only(render_app):
    client, home = render_app
    resp = client.get("/api/active")
    assert resp.status_code == 200
    data = resp.json()
    available_names = [p["name"] for p in data["available"]]
    assert "a" in available_names
    assert "b" in available_names
    assert "c" not in available_names


def test_provider_with_missing_api_key_is_filtered(tmp_path, monkeypatch):
    """A provider whose api_key_env is not exported must NOT appear in the dropdown."""
    home = tmp_path / "cc-i18n-proxy"
    home.mkdir()
    (home / "providers.toml").write_text('''
default_chain = ["a"]
[providers.a]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_A_PRESENT"
model = "m"
display_name = "A (key set)"
[providers.b]
kind = "openai-compat"
base_url = "https://api.example.com/v1"
api_key_env = "KEY_B_MISSING"
model = "m"
display_name = "B (key NOT set)"
''')
    monkeypatch.setenv("KEY_A_PRESENT", "v")
    monkeypatch.delenv("KEY_B_MISSING", raising=False)
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))

    import scripts.render_server as rs
    importlib.reload(rs)
    client = TestClient(rs.app)
    resp = client.get("/api/active")
    names = [p["name"] for p in resp.json()["available"]]
    assert "a" in names
    assert "b" not in names
    importlib.reload(rs)


def test_get_active_returns_default_head_when_state_missing(render_app):
    client, home = render_app
    resp = client.get("/api/active")
    data = resp.json()
    assert data["active"] == "a"


def test_post_active_writes_state(render_app):
    client, home = render_app
    resp = client.post("/api/active", json={"provider": "b"})
    assert resp.status_code == 200
    state = json.loads((home / "state.json").read_text())
    assert state["active_head"] == "b"
    assert state["updated_by"] == "user_via_render_ui"


def test_post_active_invalid_provider_returns_400(render_app):
    client, home = render_app
    resp = client.post("/api/active", json={"provider": "nope"})
    assert resp.status_code == 400
    assert "not in enabled" in resp.json()["detail"].lower()


def test_post_active_disabled_provider_returns_400(render_app):
    client, home = render_app
    resp = client.post("/api/active", json={"provider": "c"})
    assert resp.status_code == 400


def test_top_bar_renders_in_index(render_app):
    client, _ = render_app
    resp = client.get("/")
    assert resp.status_code == 200
    html_content = resp.text
    assert 'class="topbar"' in html_content
    assert 'id="provider-select"' in html_content
    assert "Provider A" in html_content
    assert "Provider B" in html_content
    assert "Provider C" not in html_content


def test_top_bar_renders_in_detail(render_app):
    client, _ = render_app
    resp = client.get("/nonexistentsession")
    if resp.status_code == 200:
        assert 'class="topbar"' in resp.text


def test_top_bar_warning_when_toml_missing(tmp_path, monkeypatch):
    home = tmp_path / "cc-i18n-proxy"
    home.mkdir()
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))

    import scripts.render_server as rs
    importlib.reload(rs)
    client = TestClient(rs.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "providers.toml not loaded" in resp.text or "warning" in resp.text.lower()
    importlib.reload(rs)


def test_detail_page_substitutes_workspace_id_placeholder(tmp_path, monkeypatch):
    """Detail page must inject WORKSPACE_ID for the polling JS — no raw __WORKSPACE_ID__ leaks."""
    import importlib
    import sys
    home = tmp_path / "proxy_home"
    home.mkdir(parents=True, exist_ok=True)
    emit = tmp_path / "emit"
    emit.mkdir(parents=True, exist_ok=True)
    audit = home / "audit"
    audit.mkdir(parents=True, exist_ok=True)
    (emit / "cc-i18n-deadbeef.md").write_text("# hi", encoding="utf-8")
    monkeypatch.setenv("CC_I18N_PROXY_HOME", str(home))
    monkeypatch.setenv("CC_I18N_PROXY_EMIT_DIR", str(emit))
    monkeypatch.setenv("CC_I18N_PROXY_AUDIT_DIR", str(audit))
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    if "render_server" in sys.modules:
        del sys.modules["render_server"]
    render_server = importlib.import_module("render_server")
    from fastapi.testclient import TestClient
    client = TestClient(render_server.app)
    resp = client.get("/default/deadbeef")
    assert resp.status_code == 200
    body = resp.text
    assert 'const WORKSPACE_ID = "default"' in body
    assert 'const SESSION_ID = "deadbeef"' in body
    assert "__WORKSPACE_ID__" not in body, "placeholder must be substituted"
