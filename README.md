# cc-translate-proxy

繁中 / English bilingual sidecar proxy for [Claude Code](https://claude.com/claude-code). Type prompts in Chinese; CC sees English on the wire to Anthropic; you read the model's reply translated back to Chinese in a local browser tab.

## Why

Claude Code adapts to whatever language matches the user's last prompt. Mixing Chinese and English in the same session inflates token usage, and Chinese inference can drift in tone or precision compared to English. This proxy keeps the **CC ↔ Anthropic conversation entirely in English** (cheaper, more stable) while preserving Chinese as the input/output surface you actually read and type.

## Screenshots

> _Screenshots coming soon._

## How it works

```
You type 中文
   │
   ▼
cc-translate-proxy intercepts /v1/messages
   ├─ translates 中文 → English (Gemini Flash / Groq / OpenRouter, with failover)
   ├─ forwards English to api.anthropic.com
   └─ forks the English reply → translates back to 中文 → renders in a local web UI

CC sees a clean English conversation; you read everything in 中文.
```

## Disclosure

- **Prompts and replies are sent to a third-party LLM** (Gemini Flash by default) for translation. Don't enable on sessions containing sensitive content.
- **Audit logs (containing translated prompts/replies) are written to local disk** under `audit/`. They're for inspection/debugging — clean them up periodically.
- **Personal experimental tool**, not production-grade. Expect rough edges.

## Quickstart

Requirements: Python 3.12+, [`uv`](https://github.com/astral-sh/uv).

1. Clone & install:
   ```bash
   git clone https://github.com/gggodlin/cc-translate-proxy.git
   cd cc-translate-proxy
   uv sync
   ```

2. Set translator API key (one of these — chain order is Gemini → Groq → OpenRouter):
   ```bash
   export GEMINI_API_KEY=...           # default
   # OR
   export GROQ_API_KEY=...             # alternative
   # OR
   export OPENROUTER_API_KEY=...       # alternative
   ```

3. Point Claude Code at the proxy:
   ```bash
   export ANTHROPIC_BASE_URL=http://localhost:8080
   export ENABLE_TOOL_SEARCH=auto      # restore deferred MCP loading (see Caveats)
   ```

4. Start the proxy and render server:
   ```bash
   uv run python -m cc_i18n_proxy > /tmp/proxy.log 2>&1 &
   uv run python scripts/render_server.py > /tmp/render.log 2>&1 &
   ```

5. In any `claude` session, enable per-session translation by typing `/intl`.

By default the proxy runs in **passthrough mode** — it forwards everything unchanged. `/intl` opts the current session in: it generates a session UUID, adds it to the proxy's translation whitelist, and you can open `http://localhost:9090/<uuid>` to read the translated rendering. `/normal` exits translation mode.

## Setup the `/intl` skill

`/intl` is a Claude Code skill, not part of this proxy. Create `~/.claude/skills/intl/SKILL.md` with this minimum content:

````markdown
---
name: intl
description: Enable per-session Chinese-to-English wire translation via cc-translate-proxy.
---

Generate a 12-hex session UUID and emit a marker so the proxy whitelists this session.

```bash
UUID=$(python3 -c "import secrets; print(secrets.token_hex(6))")
echo "<cc-translate-proxy:enable uuid=\"$UUID\" />"
echo "Render UI: http://localhost:9090/$UUID"
```

The marker travels in the next outbound request. The proxy detects it and starts translating Chinese user text on the wire. Keep this skill content visible in conversation history so `/resume` and proxy restarts can recover the marker.
````

A matching `~/.claude/skills/normal/SKILL.md` should emit `<cc-translate-proxy:disable uuid="<uuid>" />` to opt out.

## Architecture

| File | Purpose |
|---|---|
| `src/cc_i18n_proxy/server.py` | FastAPI proxy intercepting `/v1/messages` |
| `src/cc_i18n_proxy/translator.py` | Translator chain (Gemini → Groq → OpenRouter failover) |
| `src/cc_i18n_proxy/providers/` | Per-provider adapters and state store |
| `src/cc_i18n_proxy/cache.py` | SQLite cache keyed on content hash |
| `src/cc_i18n_proxy/audit.py` | Append-only JSONL audit log |
| `src/cc_i18n_proxy/pipeline.py` | Translation pipeline orchestrator |
| `src/cc_i18n_proxy/intl_sentinel.py` | Per-workspace `/intl` enable signal |
| `scripts/render_server.py` | HTTP UI rendering translated turns at `:9090` |

## Caveats

- **Auto-compaction may drop the marker**: at long conversation lengths (~50+ turns) CC may compact early messages, dropping the `/intl` marker. After that, type `/intl` again to re-enable.
- **CC auto-disables ToolSearch on non-first-party hosts**. Set `ENABLE_TOOL_SEARCH=auto` to restore deferred MCP tool loading. The proxy forwards `tool_reference` blocks unchanged so this is safe.
- **Translation isn't free**: Gemini Flash is cheap but not zero. Heavy users should monitor cost; provider failover helps when one rate-limits.

## Test

```bash
uv run pytest -v
```

## License

MIT — see [LICENSE](LICENSE).
