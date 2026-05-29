"""Markdown live-preview server for cc-i18n-proxy emit files.

Run from the project root:

    uv run python scripts/render_server.py

Then open http://localhost:9090/ in any browser (cmux internal browser works).
The index lists every emit file under $CC_I18N_PROXY_EMIT_DIR (default /tmp);
clicking a session opens a live-preview that polls the file every 800ms and
re-renders the markdown with marked.js.
"""
from __future__ import annotations

import asyncio
import html
import json
import os as _os
import re as _re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel as _BaseModel

from cc_i18n_proxy.providers import (
    ProviderEntry as _ProviderEntry,
    StateStore as _StateStore,
    load_providers_config as _load_providers_config,
    write_active_head as _write_active_head,
)
from cc_i18n_proxy.server import _classify_user_text
from cc_i18n_proxy.intl_sentinel import read_last_enable as _read_last_enable


def _classify_entry_source(entry: dict) -> str:
    """Classify an audit entry's prompt source.

    New entries carry ``prompt_source`` field (written by server.py since
    Tier (f)). Legacy entries fall back to inline pattern match against
    user_zh, so old audit files work without backfill.
    """
    explicit = entry.get("prompt_source")
    if explicit:
        return explicit
    return _classify_user_text(entry.get("user_zh") or "")


EMIT_DIR = Path(_os.environ.get("CC_I18N_PROXY_EMIT_DIR", "/tmp"))
RENDER_PORT = int(_os.environ.get("CC_I18N_RENDER_PORT", "9090"))
AUDIT_DIR = Path(_os.environ.get(
    "CC_I18N_PROXY_AUDIT_DIR",
    str(Path.home() / ".cc-i18n-proxy" / "audit"),
))
PROXY_HOME = Path(_os.environ.get("CC_I18N_PROXY_HOME", str(Path.home() / ".cc-i18n-proxy")))


def _list_sessions() -> list[tuple[str, float, int]]:
    """Return [(session_id, mtime, size_bytes), ...] sorted newest first."""
    if not EMIT_DIR.exists():
        return []
    items: list[tuple[str, float, int]] = []
    for p in EMIT_DIR.glob("cc-i18n-*.md"):
        sid = p.stem.removeprefix("cc-i18n-")
        st = p.stat()
        items.append((sid, st.st_mtime, st.st_size))
    items.sort(key=lambda x: -x[1])
    return items


def _read_session_workspace(session_id: str) -> tuple[str, str]:
    """Return (workspace_id, workspace_name) for a session, or ("default","default")."""
    audit_path = AUDIT_DIR / f"{session_id}.jsonl"
    if not audit_path.exists():
        return "default", "default"
    try:
        with audit_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ws_id = entry.get("workspace_id") or "default"
                ws_name = entry.get("workspace_name") or "default"
                return ws_id, ws_name
    except OSError:
        pass
    return "default", "default"


def _list_sessions_by_workspace() -> dict[str, list[tuple[str, str, float, int]]]:
    """Return {workspace_id: [(session_id, workspace_name, mtime, size), ...]}."""
    by_ws: dict[str, list[tuple[str, str, float, int]]] = {}
    for sid, mtime, size in _list_sessions():
        ws_id, ws_name = _read_session_workspace(sid)
        by_ws.setdefault(ws_id, []).append((sid, ws_name, mtime, size))
    return by_ws


def _safe_session_path(session: str) -> Path:
    if "/" in session or ".." in session or not session:
        raise HTTPException(status_code=400, detail="invalid session id")
    return EMIT_DIR / f"cc-i18n-{session}.md"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _reload_providers_cache()
    yield


app = FastAPI(title="cc-i18n-render", lifespan=_lifespan)

PROVIDERS_CACHE: dict = {"providers": {}, "default_chain": [], "load_error": None}
STATE_STORE = _StateStore(PROXY_HOME / "state.json")


def _reload_providers_cache() -> None:
    PROVIDERS_CACHE["load_error"] = None
    PROVIDERS_CACHE["providers"] = {}
    PROVIDERS_CACHE["default_chain"] = []
    toml_path = PROXY_HOME / "providers.toml"
    if not toml_path.exists():
        PROVIDERS_CACHE["load_error"] = f"providers.toml not loaded — file missing at {toml_path}"
        return
    try:
        cfg = _load_providers_config(toml_path, dotenv_path=PROXY_HOME / ".env")
    except Exception as exc:
        PROVIDERS_CACHE["load_error"] = f"providers.toml not loaded: {exc}"
        return
    listed: dict[str, _ProviderEntry] = {}
    for name, p in cfg.providers.items():
        if not p.enabled:
            continue
        if p.api_key_env and not _os.environ.get(p.api_key_env):
            continue
        listed[name] = p
    PROVIDERS_CACHE["providers"] = listed
    PROVIDERS_CACHE["default_chain"] = list(cfg.default_chain)


_reload_providers_cache()


def _top_bar_html(session_tabs_html: str = "", include_status: bool = False) -> str:
    tabs_block = (
        f'<nav class="session-tabs">{session_tabs_html}</nav>'
        if session_tabs_html else ""
    )
    status_widget = (
        '<span id="status" class="status-widget">connecting…</span>'
        if include_status else ""
    )
    if PROVIDERS_CACHE.get("load_error"):
        return f'''
<header class="topbar">
  <a href="/" class="title">cc-i18n render</a>
  {tabs_block}
  <div class="widgets">
    {status_widget}
    <span class="warning">⚠️ {html.escape(PROVIDERS_CACHE["load_error"])}</span>
  </div>
</header>
'''
    active_head = STATE_STORE.read_active_head() or (
        PROVIDERS_CACHE["default_chain"][0] if PROVIDERS_CACHE["default_chain"] else ""
    )
    options = []
    for name, p in PROVIDERS_CACHE["providers"].items():
        selected = " selected" if name == active_head else ""
        options.append(
            f'<option value="{html.escape(name)}"{selected}>{html.escape(p.display_name)}</option>'
        )
    options_html = "\n".join(options)
    return f'''
<header class="topbar">
  <a href="/" class="title">cc-i18n render</a>
  {tabs_block}
  <div class="widgets">
    {status_widget}
    <div class="active-model-widget">
      <span class="label">Active:</span>
      <select id="provider-select" onchange="_setActive(this.value)">
{options_html}
      </select>
    </div>
  </div>
</header>
<script>
async function _setActive(provider) {{
  const res = await fetch('/api/active', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{provider}})
  }});
  if (!res.ok) {{
    const err = await res.json();
    alert('Switch failed: ' + err.detail);
    location.reload();
  }}
}}
</script>
'''


_TOPBAR_CSS = '''
header.topbar { grid-area: top;
  margin: 0;
  background: rgba(20, 20, 20, 0.85); backdrop-filter: blur(8px);
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.6em 1em; color: #eee;
  border-bottom: 1px solid rgba(255,255,255,0.1); }
header.topbar a.title { color: #eee; text-decoration: none; font-weight: 600; }
header.topbar .widgets { display: flex; gap: 1em; align-items: center; }
header.topbar .label { opacity: 0.6; margin-right: 0.4em; font-size: 0.9em; }
header.topbar #provider-select { background: rgba(255,255,255,0.05);
  color: #eee; border: 1px solid rgba(255,255,255,0.2); padding: 0.3em 0.5em;
  border-radius: 4px; font-size: 0.9em; }
header.topbar .warning { color: #ffb454; font-size: 0.9em; }
.turn-meta { display: flex; gap: 0.5em; padding: 0.3em 0.5em; font-size: 0.85em;
  color: #aaa; border-bottom: 1px solid rgba(255,255,255,0.05); }
.turn-id { opacity: 0.6; min-width: 4em; }
.badge { padding: 0.1em 0.4em; border-radius: 3px;
  background: rgba(255,255,255,0.05); }
.badge-error { color: #ff7777; background: rgba(255,80,80,0.1); }
.badge-legacy { color: #888; }
.error-banner {
  background: rgba(220, 53, 69, 0.12);
  border: 1px solid rgba(220, 53, 69, 0.4);
  border-radius: 4px;
  padding: 0.5em 0.8em;
  margin: 0.5em 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5em;
}
.error-msg { color: #c0392b; font-size: 0.9em; }
.retry-btn {
  background: #c0392b; color: white; border: none;
  border-radius: 3px; padding: 0.3em 0.8em; cursor: pointer; font-size: 0.85em;
}
.retry-btn:hover { background: #a02818; }
.retry-btn:disabled { opacity: 0.5; cursor: wait; }
.retry-success-note { color: #2e8b57; font-size: 0.85em; opacity: 0.8; }
header.topbar nav.session-tabs {
  flex: 1 1 0; min-width: 0;
  display: flex; gap: 0.4em; overflow-x: auto;
  font-size: 0.85em;
  padding: 0 0.6em;
}
.session-tab {
  flex-shrink: 0; padding: 0.25em 0.7em;
  background: rgba(255,255,255,0.05); border-radius: 3px;
  color: #ccc; text-decoration: none;
  white-space: nowrap; max-width: 16em; overflow: hidden; text-overflow: ellipsis;
  font-size: 0.95em;
}
.session-tab:hover { background: rgba(255,255,255,0.1); color: #fff; }
.session-tab.active {
  background: rgba(155, 188, 224, 0.2); color: #9bbce0;
  border: 1px solid rgba(155, 188, 224, 0.4);
}
.workspace-section { margin: 1.5em 0; }
.workspace-section h2 { font-size: 1.1em; margin: 0 0 0.5em; }
.workspace-section h2 a { color: inherit; text-decoration: none; }
.workspace-section h2 a:hover { text-decoration: underline; }
.recap-btn {
  position: sticky; bottom: 4.6em; display: block; margin-left: auto; margin-right: 1.2em; z-index: 10;
  background: rgba(155, 188, 224, 0.92); color: #1e1e1e;
  border: none; border-radius: 999px;
  padding: 0.55em 1em; cursor: pointer; font-size: 0.9em;
  box-shadow: 0 2px 8px rgba(0,0,0,0.18);
  transition: background 0.15s;
}
.recap-btn:hover { background: rgba(155, 188, 224, 1); }
.recap-btn.hidden { display: none; }
.recap-panel {
  position: sticky; bottom: 7.6em; display: block; margin-left: auto; margin-right: 1.2em; z-index: 9;
  background: rgba(40, 44, 52, 0.96); color: #e5e5e5;
  border: 1px solid rgba(155, 188, 224, 0.3);
  border-radius: 8px; padding: 0.9em 1em;
  width: min(420px, calc(100vw - 2.4em));
  max-height: 60vh; overflow-y: auto;
  backdrop-filter: blur(10px);
  box-shadow: 0 4px 16px rgba(0,0,0,0.32);
  font-size: 0.92em; line-height: 1.55;
}
.recap-panel.hidden { display: none; }
.recap-header { display: flex; justify-content: space-between; align-items: center;
  font-weight: 600; margin-bottom: 0.5em; padding-bottom: 0.4em;
  border-bottom: 1px solid rgba(255,255,255,0.1); }
.recap-close { background: none; border: none; color: #999; cursor: pointer;
  font-size: 1.3em; padding: 0 0.2em; line-height: 1; }
.recap-close:hover { color: #fff; }
.recap-meta { font-size: 0.8em; color: #999; margin-bottom: 0.5em; }
.recap-body { white-space: pre-wrap; }
.scroll-bottom-btn {
  position: sticky; bottom: 1.2em; display: block; margin-left: auto; margin-right: 1.2em; z-index: 10;
  background: rgba(40, 44, 52, 0.92); color: #eee;
  border: 1px solid rgba(255,255,255,0.18);
  border-radius: 999px;
  width: 2.4em; height: 2.4em;
  cursor: pointer; font-size: 1em;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 2px 8px rgba(0,0,0,0.18);
  transition: background 0.15s;
}
.scroll-bottom-btn:hover { background: rgba(60, 64, 72, 1); }
.scroll-bottom-btn.hidden { display: none; }
.sticky-prompt-bar {
  border-left: 3px solid #a0b8d0;
  background: rgba(160, 184, 208, 0.12);
  color: #6a737d;
  padding: 0.4em 1em 0.4em 0.85em;
  font-size: 0.92em;
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  backdrop-filter: blur(8px);
  transition: background 0.15s;
}
.sticky-prompt-bar:hover { background: rgba(160, 184, 208, 0.22); }
.sticky-prompt-bar.hidden { display: none; }
.sticky-prompt-top {
  position: sticky;
  top: 0;
  z-index: 90;
  margin: 0 -1em;
  padding-left: 1.85em;
}
.sticky-prompt-bottom {
  position: sticky;
  bottom: 0;
  z-index: 90;
  margin: 0 -1em;
  padding-left: 1.85em;
}
#output blockquote[id^="user-prompt-"] {
  scroll-margin-top: 5.5em;
}
@media (prefers-color-scheme: dark) {
  .sticky-prompt-bar {
    color: #aaa;
    border-left-color: #4a6580;
    background: rgba(74, 101, 128, 0.18);
  }
  .sticky-prompt-bar:hover { background: rgba(74, 101, 128, 0.3); }
}
'''


class _SetActiveReq(_BaseModel):
    provider: str


@app.get("/api/active")
def api_active_get():
    if PROVIDERS_CACHE.get("load_error"):
        return {
            "active": "",
            "active_display": "",
            "available": [],
            "error": PROVIDERS_CACHE["load_error"],
        }
    state = STATE_STORE.read_full_state() or {}
    active = state.get("active_head") or (
        PROVIDERS_CACHE["default_chain"][0] if PROVIDERS_CACHE["default_chain"] else ""
    )
    active_display = ""
    if active in PROVIDERS_CACHE["providers"]:
        active_display = PROVIDERS_CACHE["providers"][active].display_name
    return {
        "active": active,
        "active_display": active_display,
        "available": [
            {"name": name, "display": p.display_name}
            for name, p in PROVIDERS_CACHE["providers"].items()
        ],
        "updated_at": state.get("updated_at", ""),
        "updated_by": state.get("updated_by", ""),
    }


@app.post("/api/active")
def api_active_post(req: _SetActiveReq):
    if req.provider not in PROVIDERS_CACHE["providers"]:
        raise HTTPException(
            status_code=400,
            detail=f"provider {req.provider!r} not in enabled providers",
        )
    _write_active_head(PROXY_HOME / "state.json", req.provider, updated_by="user_via_render_ui")
    STATE_STORE._cached_mtime = -1
    return api_active_get()


@app.get("/api/last-enable")
def api_last_enable_get(workspace: str = ""):
    """Return the most-recent /intl enable sentinel for `workspace`, or {} if absent.

    `workspace` is required. We default to "" so a missing param produces a 400
    (rejecting it explicitly) instead of silently leaking another workspace's data.
    `_SAFE_NAME_RE` is the same pattern used by the existing path-segment validators.
    """
    if not workspace or not _SAFE_NAME_RE.match(workspace):
        raise HTTPException(status_code=400, detail="invalid or missing workspace")
    data = _read_last_enable(PROXY_HOME, workspace_id=workspace)
    if data is None:
        return {}
    return data


@app.get("/api/session/{session}/turns")
def api_session_turns(session: str):
    if "/" in session or ".." in session or not session:
        raise HTTPException(status_code=400, detail="invalid session id")
    audit_path = AUDIT_DIR / f"{session}.jsonl"
    if not audit_path.exists():
        raise HTTPException(status_code=404, detail="session not found")
    turns: list[dict] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        providers = entry.get("translation_providers", {}) or {}
        providers_display = {
            direction: PROVIDERS_CACHE["providers"][p].display_name
            if p and p in PROVIDERS_CACHE["providers"] else p or ""
            for direction, p in providers.items()
        }
        status = entry.get("translation_status", {})
        if isinstance(status, str):
            status = {"user": status, "assistant": status}
        turns.append({
            "turn_id": entry.get("turn_id"),
            "timestamp": entry.get("timestamp"),
            "translation_providers": providers,
            "translation_providers_display": providers_display,
            "failover_attempts": entry.get("failover_attempts", {}) or {},
            "failover_errors": entry.get("failover_errors", {}) or {},
            "translation_status": status,
            "retry_of": entry.get("retry_of"),
            "prompt_source": _classify_entry_source(entry),
        })
    return turns


def _first_entry_by_source(audit_dir: Path, session_id: str, source: str) -> dict | None:
    p = audit_dir / f"{session_id}.jsonl"
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _classify_entry_source(entry) == source:
                    return entry
    except OSError:
        pass
    return None


def _latest_entry_by_source(audit_dir: Path, session_id: str, source: str) -> dict | None:
    p = audit_dir / f"{session_id}.jsonl"
    if not p.exists():
        return None
    latest = None
    try:
        with p.open(encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _classify_entry_source(entry) == source:
                    latest = entry
    except OSError:
        pass
    return latest


def _session_tab_label(audit_dir: Path, session_id: str, limit: int = 40) -> str:
    """Tab label fallback chain: latest recap → first human → sid[:8]."""
    recap = _latest_entry_by_source(audit_dir, session_id, "recap")
    if recap and (recap.get("assistant_zh") or "").strip():
        return recap["assistant_zh"][:limit]
    human = _first_entry_by_source(audit_dir, session_id, "human")
    if human and (human.get("user_zh") or "").strip():
        return human["user_zh"][:limit]
    return f"{session_id[:8]}…"


@app.get("/api/session/{session}/recap/latest")
def api_session_recap_latest(session: str):
    if "/" in session or ".." in session or not session:
        raise HTTPException(400, "invalid session id")
    audit_path = AUDIT_DIR / f"{session}.jsonl"
    if not audit_path.exists():
        raise HTTPException(404, "session not found")
    entry = _latest_entry_by_source(AUDIT_DIR, session, "recap")
    if not entry:
        raise HTTPException(404, "no recap turns")
    return {
        "user_zh": entry.get("user_zh") or "",
        "assistant_zh": entry.get("assistant_zh") or "",
        "timestamp": entry.get("timestamp") or "",
        "turn_id": entry.get("turn_id"),
    }


@app.post("/api/session/{session}/turns/{turn_id}/retry")
async def api_retry_turn(session: str, turn_id: int, body: dict | None = None):
    """Proxy retry request to the proxy daemon (which has the chain)."""
    import httpx as _httpx
    payload = {
        "session": session,
        "turn_id": turn_id,
        "head": (body or {}).get("head", ""),
    }
    try:
        async with _httpx.AsyncClient(base_url="http://localhost:8080", timeout=30.0) as client:
            resp = await client.post("/v1/internal/retry", json=payload)
    except _httpx.RequestError as exc:
        raise HTTPException(502, f"proxy daemon unreachable: {exc}")
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)
    return resp.json()


_INDEX_CSS = """
  body { font-family: -apple-system, "PingFang TC", sans-serif;
         max-width: none; margin: 1em 0; padding: 0 1em; line-height: 1.6; }
  table { border-collapse: collapse; width: 100%; }
  th, td { padding: 0.4em 0.8em; text-align: left; border-bottom: 1px solid #ddd; }
  a { color: #5a7896; text-decoration: none; }
  a:hover { color: #3d5a76; text-decoration: underline; }
  .muted { color: #888; font-size: 0.9em; }
  @media (prefers-color-scheme: dark) {
    body { background: #1e1e1e; color: #e5e5e5; }
    th, td { border-bottom-color: #444; }
    a { color: #9bbce0; }
    a:hover { color: #bcd6ed; }
    .muted { color: #999; }
  }
"""

_INDEX_HEAD = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>cc-i18n render</title>
<style>
__INDEX_CSS__
__TOPBAR_CSS__
</style>
</head>
<body>
__TOPBAR_HTML__
<h1>cc-i18n sessions</h1>
<p class="muted">Watching <code>$EMIT_DIR</code>. Auto-refreshes every 3s.</p>
"""


def _wrap_index_html(inner_body: str) -> str:
    return (
        f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8"><title>cc-i18n render</title>
<style>
{_INDEX_CSS}
{_TOPBAR_CSS}
</style>
</head>
<body>
{_top_bar_html()}
{inner_body}
</body></html>"""
    )


_SAFE_NAME_RE = _re.compile(r"^[A-Za-z0-9_\-]{1,128}$")
_SAFE_SESSION = _re.compile(r"^[a-fA-F0-9]{1,64}$")


def _workspace_overview_row(workspace_id: str, sid: str, mtime: float, size: int, preview: str) -> str:
    ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return (
        f'<tr><td><a href="/{html.escape(workspace_id)}/{html.escape(sid)}">'
        f'{html.escape(sid[:8])}…</a></td>'
        f'<td>{html.escape(preview)}</td>'
        f'<td class="muted">{ts}</td>'
        f'<td class="muted">{size:,}B</td></tr>'
    )


def _session_strip_html(workspace: str, current_session: str) -> str:
    """Return raw session tab `<a>` HTML (no wrapper) for embedding inside topbar."""
    by_ws = _list_sessions_by_workspace()
    sessions = by_ws.get(workspace, [])
    if len(sessions) <= 1:
        return ""
    tabs = []
    for sid, _ws_name, _mtime, _size in sessions:
        preview = _session_tab_label(AUDIT_DIR, sid)
        cls = "session-tab active" if sid == current_session else "session-tab"
        tabs.append(
            f'<a href="/{html.escape(workspace)}/{html.escape(sid)}" class="{cls}" title="{html.escape(sid)}">'
            f'{html.escape(preview)}</a>'
        )
    return "".join(tabs)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    by_ws = _list_sessions_by_workspace()

    if len(by_ws) <= 1:
        sessions = []
        for ws_sessions in by_ws.values():
            for sid, _ws_name, mtime, size in ws_sessions:
                sessions.append((sid, mtime, size))
        sessions.sort(key=lambda x: -x[1])
        if not sessions:
            rows = '<tr><td colspan="4" class="muted">No emit files yet — run a CC command through the proxy first.</td></tr>'
        else:
            now = datetime.now(timezone.utc).timestamp()
            rows_list = []
            for sid, mtime, size in sessions:
                preview = _session_tab_label(AUDIT_DIR, sid)
                preview_html = html.escape(preview)
                delta = max(0, now - mtime)
                if delta < 60:
                    rel = f"{int(delta)}s ago"
                elif delta < 3600:
                    rel = f"{int(delta / 60)}m ago"
                elif delta < 86400:
                    rel = f"{int(delta / 3600)}h ago"
                else:
                    rel = f"{int(delta / 86400)}d ago"
                sid_attr = html.escape(sid, quote=True)
                sid_text = html.escape(sid)
                rows_list.append(
                    f'<tr><td>{preview_html}</td>'
                    f'<td><a href="/{sid_attr}">{sid_text}</a></td>'
                    f'<td>{rel}</td>'
                    f'<td class="muted">{size} B</td></tr>'
                )
            rows = "\n".join(rows_list)
        body = (
            _INDEX_HEAD
            .replace("$EMIT_DIR", str(EMIT_DIR))
            .replace("__INDEX_CSS__", _INDEX_CSS)
            .replace("__TOPBAR_CSS__", _TOPBAR_CSS)
            .replace("__TOPBAR_HTML__", _top_bar_html())
        )
        body += (
            '<table>'
            '<tr><th>Preview</th><th>Session</th><th>Updated</th><th>Size</th></tr>'
            f'{rows}</table>'
        )
        body += '<script>setTimeout(()=>location.reload(), 3000);</script></body></html>'
        return body

    sorted_workspaces = sorted(
        by_ws.items(),
        key=lambda kv: -max(s[2] for s in kv[1]) if kv[1] else 0,
    )
    sections = []
    for ws_id, ws_sessions in sorted_workspaces:
        if not ws_sessions:
            continue
        ws_name = ws_sessions[0][1] if ws_sessions[0][1] else ws_id
        rows = []
        for sid, _ws, mtime, size in ws_sessions[:50]:
            preview = _session_tab_label(AUDIT_DIR, sid)
            rows.append(_workspace_overview_row(ws_id, sid, mtime, size, preview))
        sections.append(
            f'<section class="workspace-section">'
            f'<h2><a href="/{html.escape(ws_id)}">{html.escape(ws_name)}</a> '
            f'<span class="muted">({len(ws_sessions)} sessions)</span></h2>'
            f'<table><tbody>{"".join(rows)}</tbody></table>'
            f'</section>'
        )
    inner = (
        f'<h1>cc-i18n sessions</h1>'
        f'<p class="muted">Watching <code>{html.escape(str(EMIT_DIR))}</code>. Auto-refreshes every 3s.</p>'
        + "\n".join(sections)
        + '<script>setTimeout(()=>location.reload(), 3000);</script>'
    )
    return _wrap_index_html(inner)


_RENDER_TEMPLATE_BASE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>cc-i18n: __SESSION__</title>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<style>
  html, body {
    height: 100vh;
    margin: 0;
    overflow: hidden;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, "PingFang TC", "Segoe UI", sans-serif;
         max-width: none; line-height: 1.65;
         color: #24292e;
         display: grid;
         grid-template-rows: auto 1fr auto;
         grid-template-areas: "top" "main" "bottom"; }
  main.chat-area {
    grid-area: main;
    overflow-y: auto;
    position: relative;
    padding: 0 1em;
  }
  pre { background: #f4f4f4; padding: 0.8em; border-radius: 4px;
        overflow-x: auto; font-size: 0.92em; }
  code { background: #f4f4f4; padding: 0.1em 0.35em; border-radius: 3px;
         font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  pre code { padding: 0; background: none; }
  h1, h2 { border-bottom: 1px solid #e1e4e8; padding-bottom: 0.3em; margin-top: 1.6em; }
  a { color: #5a7896; text-decoration: none; }
  a:hover { color: #3d5a76; text-decoration: underline; }
  blockquote { border-left: 3px solid #a0b8d0; padding: 0 1em; color: #6a737d;
               margin: 1em 0; background: rgba(160, 184, 208, 0.06); }
  hr { border: none; border-top: 1px solid #e5e8eb; opacity: 0.5; margin: 1.5em 0; }
  table { border-collapse: collapse; margin: 1em 0; }
  th, td { padding: 0.4em 0.8em; text-align: left; border: 1px solid #d1d5da; }
  th { background: #f6f8fa; font-weight: 600; }
  #status.status-widget { font-size: 11px; padding: 0.2em 0.55em;
            background: rgba(255,255,255,0.05); color: #aaa;
            border-radius: 3px; border: 1px solid rgba(255,255,255,0.1);
            font-family: ui-monospace, "SF Mono", Menlo, monospace;
            white-space: nowrap; }
  #header { display: flex; align-items: baseline; gap: 1em;
            border-bottom: 2px solid #d1d5da; padding-bottom: 0.5em; margin-bottom: 1em; }
  #header h1 { border: none; margin: 0; font-size: 1.4em; }
  #header a { font-size: 0.9em; color: #586069; text-decoration: none; }
  #header a:hover { color: #5a7896; }
  @media (prefers-color-scheme: dark) {
    body { background: #1e1e1e; color: #e5e5e5; }
    pre, code { background: #2d2d2d; }
    h1, h2 { border-bottom-color: #444; }
    a { color: #9bbce0; }
    a:hover { color: #bcd6ed; }
    blockquote { color: #aaa; border-left-color: #4a6580;
                 background: rgba(74, 101, 128, 0.12); }
    hr { border-top-color: #444; opacity: 0.4; }
    th, td { border-color: #444; }
    th { background: #2d2d2d; }
    #header { border-bottom-color: #444; }
    #header a { color: #999; }
    #header a:hover { color: #9bbce0; }
  }
__TOPBAR_CSS__
</style>
</head>
<body>
__TOPBAR_HTML__
<main class="chat-area" id="chat-area">
<div id="sticky-prompt-top" class="sticky-prompt-bar sticky-prompt-top hidden" role="button" tabindex="0" onclick="_scrollToStickyTarget('sticky-prompt-top')"></div>
<div id="turns-meta"></div>
<div id="header">
  <h1>__SESSION__</h1>
  <a href="/">← all sessions</a>
</div>
<div id="output"><em class="muted">Loading…</em></div>
<div id="sticky-prompt-bottom" class="sticky-prompt-bar sticky-prompt-bottom hidden" role="button" tabindex="0" onclick="_scrollToStickyTarget('sticky-prompt-bottom')"></div>
<button id="recap-btn" class="recap-btn hidden" onclick="_toggleRecap()">🔄 recap</button>
<div id="recap-panel" class="recap-panel hidden" role="dialog" aria-label="latest recap">
  <div class="recap-header">
    <span>🔄 最新 recap</span>
    <button class="recap-close" onclick="_toggleRecap()" aria-label="close">×</button>
  </div>
  <div id="recap-meta" class="recap-meta"></div>
  <div id="recap-body" class="recap-body"></div>
</div>
<button id="scroll-bottom-btn" class="scroll-bottom-btn hidden" onclick="_scrollToBottom()" aria-label="scroll to bottom" title="scroll to bottom">⬇</button>
</main>
<script>
const _chatArea = () => document.getElementById('chat-area');
const SESSION_ID = "__SESSION_ID__";
const WORKSPACE_ID = "__WORKSPACE_ID__";
const _LAST_ENABLE_URL = '/api/last-enable?workspace=' + encodeURIComponent(WORKSPACE_ID);

let _lastEnableBaselineTs = 0;

async function _initLastEnableBaseline() {
  try {
    const resp = await fetch(_LAST_ENABLE_URL, { cache: 'no-store' });
    if (!resp.ok) return;
    const data = await resp.json();
    if (data && typeof data.ts === 'number') {
      _lastEnableBaselineTs = data.ts;
    }
  } catch (_e) { /* tolerate offline at load — baseline stays 0 */ }
}

async function _pollLastEnable() {
  try {
    const resp = await fetch(_LAST_ENABLE_URL, { cache: 'no-store' });
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data || typeof data.ts !== 'number') return;
    if (data.ts <= _lastEnableBaselineTs) return;
    if (data.session_id && data.session_id !== SESSION_ID) {
      location.replace('/' + encodeURIComponent(WORKSPACE_ID) + '/' + encodeURIComponent(data.session_id));
      return;
    }
    _lastEnableBaselineTs = data.ts;
  } catch (_e) { /* silent — proxy may be transiently offline */ }
}

_initLastEnableBaseline().then(() => setInterval(_pollLastEnable, 1500));

function _badge(arrow, name, failoverCount, status) {
  let classes = 'badge';
  let suffix = '';
  if (status === 'translator_config_error') {
    classes += ' badge-error';
    return `<span class="${classes}">${arrow} ❌ config error</span>`;
  }
  if (status === 'translate_api_outage') {
    classes += ' badge-error';
    return `<span class="${classes}">${arrow} ⚠️ all providers failed</span>`;
  }
  if (failoverCount > 0) suffix = ' ⚠️';
  if (!name) {
    classes += ' badge-legacy';
    return `<span class="${classes}">${arrow} (legacy)</span>`;
  }
  return `<span class="${classes}">${arrow} ${name}${suffix}</span>`;
}

async function _loadTurns() {
  const resp = await fetch(`/api/session/${SESSION_ID}/turns`);
  if (!resp.ok) return;
  const turns = await resp.json();

  // Build retry_index: original_turn_id → most-recent retry turn
  const retryIndex = {};
  for (const t of turns) {
    if (t.retry_of != null) {
      retryIndex[t.retry_of] = t;
    }
  }

  const container = document.getElementById('turns-meta');
  container.innerHTML = turns.filter(t => t.retry_of == null).map((t, idx) => {
    const userP = t.translation_providers_display && t.translation_providers_display.user || '(none)';
    const asstP = t.translation_providers_display && t.translation_providers_display.assistant || '(none)';
    const userF = (t.failover_attempts && t.failover_attempts.user || []).length;
    const asstF = (t.failover_attempts && t.failover_attempts.assistant || []).length;
    const userS = t.translation_status && t.translation_status.user || '';
    const asstS = t.translation_status && t.translation_status.assistant || '';

    let errorBanner = '';
    const retry = retryIndex[t.turn_id];
    if (asstS !== 'ok') {
      if (retry && retry.translation_status && retry.translation_status.assistant === 'ok') {
        const provName = (retry.translation_providers_display && retry.translation_providers_display.assistant) || (retry.translation_providers && retry.translation_providers.assistant) || '';
        errorBanner = '<div class="retry-success-note">(重試成功，使用 ' + _escape(provName) + ')</div>';
      } else {
        const failErr = (t.failover_errors && t.failover_errors.assistant && t.failover_errors.assistant[0]) || {};
        const errCode = failErr.code != null ? failErr.code : '?';
        const errProv = failErr.provider || '?';
        const errMsg = (failErr.message || '').slice(0, 120);
        errorBanner = '<div class="error-banner">' +
          '<span class="error-msg">⚠️ assistant: ' + _escape(errCode) + ' from ' + _escape(errProv) + ': ' + _escape(errMsg) + '</span>' +
          '<button class="retry-btn" onclick="_retryTurn(' + t.turn_id + ', this)">重試</button>' +
        '</div>';
      }
    }

    const isHealthy = asstS === 'ok' && userS === 'ok' && userF === 0 && asstF === 0;
    const turnMeta = isHealthy ? '' :
      '<div class="turn-meta">' +
      '<span class="turn-id">turn ' + (t.turn_id != null ? t.turn_id : idx+1) + '</span>' +
      _badge('→', userP, userF, userS) +
      _badge('←', asstP, asstF, asstS) +
      '</div>';
    return turnMeta + errorBanner;
  }).join('');
}

function _escape(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function _retryTurn(turnId, btn) {
  btn.disabled = true;
  btn.textContent = "重試中...";
  try {
    const resp = await fetch(`/api/session/${SESSION_ID}/turns/${turnId}/retry`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({}),
    });
    if (resp.ok) {
      location.reload();
    } else {
      const err = await resp.json().catch(() => ({}));
      alert("重試失敗: " + (err.detail || resp.statusText));
      btn.disabled = false;
      btn.textContent = "重試";
    }
  } catch (e) {
    alert("重試失敗: " + e.message);
    btn.disabled = false;
    btn.textContent = "重試";
  }
}

async function _loadRecap() {
  try {
    const resp = await fetch(`/api/session/${SESSION_ID}/recap/latest`);
    if (!resp.ok) return;
    const data = await resp.json();
    const body = data.assistant_zh || '';
    if (!body.trim()) return;
    document.getElementById('recap-btn').classList.remove('hidden');
    const meta = document.getElementById('recap-meta');
    meta.textContent = data.timestamp
      ? new Date(data.timestamp).toLocaleString()
      : '';
    document.getElementById('recap-body').textContent = body;
  } catch (e) { /* silent — no recap is fine */ }
}

function _toggleRecap() {
  document.getElementById('recap-panel').classList.toggle('hidden');
}

const PROMPT_EMOJI = '👤';
const PREVIEW_MAX_CHARS = 60;

function _isUserPromptBlockquote(bq) {
  return bq.textContent.trim().startsWith(PROMPT_EMOJI);
}

function _previewText(bq) {
  const raw = bq.textContent.replace(PROMPT_EMOJI, '').trim().replace(/\\s+/g, ' ');
  if (raw.length <= PREVIEW_MAX_CHARS) return raw;
  return raw.slice(0, PREVIEW_MAX_CHARS) + '…';
}

function _topbarOffset() {
  const tb = document.querySelector('header.topbar');
  return tb ? tb.offsetHeight : 0;
}

function _userPromptBlockquotes() {
  const out = document.getElementById('output');
  if (!out) return [];
  return Array.from(out.querySelectorAll('blockquote')).filter(_isUserPromptBlockquote);
}

function _ensurePromptIds() {
  _userPromptBlockquotes().forEach((bq, idx) => {
    if (!bq.id) bq.id = 'user-prompt-' + idx;
  });
}

function _updateTopbarVar() {
  const h = _topbarOffset();
  if (h > 0) document.documentElement.style.setProperty('--topbar-height', h + 'px');
}

function _updateStickyBars() {
  const topBar = document.getElementById('sticky-prompt-top');
  const bottomBar = document.getElementById('sticky-prompt-bottom');
  if (!topBar || !bottomBar) return;
  const blocks = _userPromptBlockquotes();
  if (blocks.length === 0) {
    topBar.classList.add('hidden');
    bottomBar.classList.add('hidden');
    return;
  }
  const STICKY_BAR_BUDGET = 44;
  const topboundary = _topbarOffset() + STICKY_BAR_BUDGET;
  const ca = _chatArea();
  const viewBottom = ca ? ca.getBoundingClientRect().bottom : window.innerHeight;
  let above = null;
  let below = null;
  for (const bq of blocks) {
    const rect = bq.getBoundingClientRect();
    if (rect.bottom <= topboundary) {
      above = bq;
    } else if (rect.top >= viewBottom - 4 && below == null) {
      below = bq;
    }
  }
  if (above) {
    topBar.textContent = PROMPT_EMOJI + ' ' + _previewText(above);
    topBar.dataset.targetId = above.id;
    topBar.classList.remove('hidden');
  } else {
    topBar.classList.add('hidden');
  }
  if (below) {
    bottomBar.textContent = PROMPT_EMOJI + ' ' + _previewText(below);
    bottomBar.dataset.targetId = below.id;
    bottomBar.classList.remove('hidden');
  } else {
    bottomBar.classList.add('hidden');
  }
}

function _scrollToStickyTarget(barId) {
  const bar = document.getElementById(barId);
  if (!bar) return;
  const tid = bar.dataset.targetId;
  if (!tid) return;
  const target = document.getElementById(tid);
  if (!target) return;
  target.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function _updateScrollBottomBtn() {
  const btn = document.getElementById('scroll-bottom-btn');
  if (!btn) return;
  const ca = _chatArea();
  const atBottom = ca
    ? (ca.scrollTop + ca.clientHeight >= ca.scrollHeight - 60)
    : true;
  btn.classList.toggle('hidden', atBottom);
}

function _scrollToBottom() {
  const ca = _chatArea();
  if (ca) ca.scrollTo({top: ca.scrollHeight, behavior: 'smooth'});
}

function _onScrollOrResize() {
  _updateStickyBars();
  _updateScrollBottomBtn();
}

document.addEventListener('DOMContentLoaded', () => {
  _loadTurns();
  _loadRecap();
  _updateTopbarVar();
  const ca = _chatArea();
  if (ca) ca.addEventListener('scroll', _onScrollOrResize, {passive: true});
  window.addEventListener('resize', () => { _updateTopbarVar(); _onScrollOrResize(); });
});

const session = "__SESSION__";
let lastLen = -1;
let lastErr = null;

async function refresh() {
  try {
    const r = await fetch("/raw/" + session + "?t=" + Date.now(), { cache: "no-store" });
    if (!r.ok) {
      setStatus(`error ${r.status}`);
      return;
    }
    const md = await r.text();
    if (md.length === lastLen) {
      setStatus(`${md.length} chars`);
      return;
    }
    lastLen = md.length;
    const out = document.getElementById("output");
    const ca = _chatArea();
    const wasAtBottom = ca
      ? (ca.scrollTop + ca.clientHeight >= ca.scrollHeight - 120)
      : true;
    out.innerHTML = md.trim() ? marked.parse(md) : '<em class="muted">(empty)</em>';
    _ensurePromptIds();
    if (wasAtBottom && ca) ca.scrollTop = ca.scrollHeight;
    _updateStickyBars();
    _updateScrollBottomBtn();
    setStatus(`${md.length} chars`);
    lastErr = null;
  } catch (e) {
    if (lastErr !== "offline") {
      setStatus("offline — proxy not running?");
      lastErr = "offline";
    }
  }
}

function setStatus(s) {
  document.getElementById("status").textContent = s;
}

setInterval(refresh, 800);
refresh();

</script>
</body>
</html>
"""


@app.get("/raw/{session}", response_class=PlainTextResponse)
async def raw(session: str) -> str:
    p = _safe_session_path(session)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _render_detail_page(session: str, *, workspace: str) -> str:
    safe_session = html.escape(session)
    safe_workspace = html.escape(workspace)
    tabs_inner = _session_strip_html(workspace, session)
    topbar_html = _top_bar_html(session_tabs_html=tabs_inner, include_status=True)
    return (
        _RENDER_TEMPLATE_BASE
        .replace("__TOPBAR_CSS__", _TOPBAR_CSS)
        .replace("__TOPBAR_HTML__", topbar_html)
        .replace("__SESSION_ID__", safe_session)
        .replace("__WORKSPACE_ID__", safe_workspace)
        .replace("__SESSION__", safe_session)
    )




@app.get("/{workspace}/{session}", response_class=HTMLResponse)
async def render_session_in_workspace(workspace: str, session: str) -> str:
    if not _SAFE_NAME_RE.match(workspace) or not _SAFE_NAME_RE.match(session):
        raise HTTPException(400, "invalid path segment")
    return _render_detail_page(session, workspace=workspace)


@app.get("/{workspace}", response_class=HTMLResponse)
async def workspace_index(workspace: str) -> str:
    if "/" in workspace or ".." in workspace or len(workspace) > 128:
        raise HTTPException(400, "invalid workspace")
    by_ws = _list_sessions_by_workspace()
    if workspace not in by_ws:
        return await render_session(workspace)
    sessions = by_ws[workspace]
    ws_name = sessions[0][1] if sessions else workspace
    rows = []
    for sid, _ws, mtime, size in sessions:
        preview = _session_tab_label(AUDIT_DIR, sid)
        rows.append(_workspace_overview_row(workspace, sid, mtime, size, preview))
    inner = (
        f'<h1>{html.escape(ws_name)}</h1>'
        f'<p class="muted">Workspace <code>{html.escape(workspace)}</code> — {len(sessions)} sessions</p>'
        f'<table><tbody>{"".join(rows)}</tbody></table>'
    )
    return _wrap_index_html(inner)


@app.get("/{session}", response_class=HTMLResponse)
async def render_session(session: str) -> str:
    if not session.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="invalid session id")
    ws_id, _ = _read_session_workspace(session)
    return _render_detail_page(session, workspace=ws_id)


def main() -> None:
    print(f"[render-server] watching {EMIT_DIR}", file=sys.stderr)
    print(f"[render-server] http://localhost:{RENDER_PORT}/", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=RENDER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
