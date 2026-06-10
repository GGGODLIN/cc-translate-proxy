"""Inline HTML/CSS/JS template constants for the render server."""

TOPBAR_CSS = '''
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


INDEX_CSS = """
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


RENDER_TEMPLATE_BASE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>cc-i18n: __SESSION__</title>
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
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
    out.innerHTML = md.trim() ? DOMPurify.sanitize(marked.parse(md)) : '<em class="muted">(empty)</em>';
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
