"""Markdown live-preview server for cc-i18n-proxy emit files.

Run from the project root:

    uv run python scripts/render_server.py

Then open http://localhost:9090/ in any browser (cmux internal browser works).
The index lists every emit file under $CC_I18N_PROXY_EMIT_DIR (default
~/.cc-i18n-proxy/emit);
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

import httpx as _httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel as _BaseModel

from cc_i18n_proxy.providers import (
    ProviderEntry as _ProviderEntry,
    StateStore as _StateStore,
    load_providers_config as _load_providers_config,
    write_active_head as _write_active_head,
)
from cc_i18n_proxy.server import _classify_user_text
from cc_i18n_proxy.intl_sentinel import read_last_enable as _read_last_enable

try:
    from scripts.render_templates import (
        INDEX_CSS as _INDEX_CSS,
        RENDER_TEMPLATE_BASE as _RENDER_TEMPLATE_BASE,
        TOPBAR_CSS as _TOPBAR_CSS,
    )
except ImportError:
    from render_templates import (
        INDEX_CSS as _INDEX_CSS,
        RENDER_TEMPLATE_BASE as _RENDER_TEMPLATE_BASE,
        TOPBAR_CSS as _TOPBAR_CSS,
    )


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


PROXY_HOME = Path(_os.environ.get("CC_I18N_PROXY_HOME", str(Path.home() / ".cc-i18n-proxy")))
EMIT_DIR = Path(_os.environ.get("CC_I18N_PROXY_EMIT_DIR", str(PROXY_HOME / "emit")))
RENDER_PORT = int(_os.environ.get("CC_I18N_RENDER_PORT", "9090"))
AUDIT_DIR = Path(_os.environ.get("CC_I18N_PROXY_AUDIT_DIR", str(PROXY_HOME / "audit")))

_SAFE_NAME_RE = _re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _validate_session_id(session: str) -> None:
    if not _SAFE_NAME_RE.match(session):
        raise HTTPException(status_code=400, detail="invalid session id")


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
    _validate_session_id(session)
    return EMIT_DIR / f"cc-i18n-{session}.md"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _reload_providers_cache()
    yield


app = FastAPI(title="cc-i18n-render", lifespan=_lifespan)

# In-memory snapshot of providers.toml, refreshed only via _reload_providers_cache.
# Safe without a lock because uvicorn runs this app on a single-process event loop;
# revisit if workers > 1 or background threads ever mutate it.
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
    STATE_STORE.invalidate()
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
    _validate_session_id(session)
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
    _validate_session_id(session)
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
    _validate_session_id(session)
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


def _index_html() -> str:
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
        inner = (
            '<h1>cc-i18n sessions</h1>'
            f'<p class="muted">Watching <code>{html.escape(str(EMIT_DIR))}</code>. Auto-refreshes every 3s.</p>'
            '<table>'
            '<tr><th>Preview</th><th>Session</th><th>Updated</th><th>Size</th></tr>'
            f'{rows}</table>'
            '<script>setTimeout(()=>location.reload(), 3000);</script>'
        )
        return _wrap_index_html(inner)

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


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return await asyncio.to_thread(_index_html)


@app.get("/raw/{session}", response_class=PlainTextResponse)
async def raw(session: str) -> str:
    p = _safe_session_path(session)
    if not p.exists():
        return ""
    try:
        return await asyncio.to_thread(p.read_text, encoding="utf-8")
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
    if not _SAFE_NAME_RE.match(workspace):
        raise HTTPException(400, "invalid path segment")
    _validate_session_id(session)
    return await asyncio.to_thread(_render_detail_page, session, workspace=workspace)


def _workspace_index_html(workspace: str) -> str | None:
    by_ws = _list_sessions_by_workspace()
    if workspace not in by_ws:
        return None
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


@app.get("/{workspace}", response_class=HTMLResponse)
async def workspace_index(workspace: str) -> str:
    if not _SAFE_NAME_RE.match(workspace):
        raise HTTPException(400, "invalid workspace")
    page = await asyncio.to_thread(_workspace_index_html, workspace)
    if page is None:
        return await render_session(workspace)
    return page


def _session_detail_html(session: str) -> str:
    ws_id, _ = _read_session_workspace(session)
    return _render_detail_page(session, workspace=ws_id)


@app.get("/{session}", response_class=HTMLResponse)
async def render_session(session: str) -> str:
    _validate_session_id(session)
    return await asyncio.to_thread(_session_detail_html, session)


def main() -> None:
    print(f"[render-server] watching {EMIT_DIR}", file=sys.stderr)
    print(f"[render-server] http://localhost:{RENDER_PORT}/", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=RENDER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
