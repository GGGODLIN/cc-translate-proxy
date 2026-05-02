"""FastAPI server with full translation pipeline."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as _BaseModel

from cc_i18n_proxy.audit import AuditLogWriter, TurnEntry
from cc_i18n_proxy.cache import TranslationCache
from cc_i18n_proxy.config import Config
from cc_i18n_proxy.emitter import FileEmitter
from cc_i18n_proxy.intl_sentinel import write_last_enable
from cc_i18n_proxy.pipeline import TranslationPipeline
from cc_i18n_proxy.translator import (
    Translator,
    TranslatorChain,
    TranslatorChainExhausted,
    TranslatorConfigError,
)

_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class _RetryReq(_BaseModel):
    session: str
    turn_id: int
    head: str = ""

log = logging.getLogger(__name__)


def _trace(event: dict[str, object]) -> None:
    """Emit one JSON-line per translation event when TRACE_TRANSLATION=1.

    Off by default to avoid log spam in normal use; opt-in via env var for
    live debugging when uvicorn's INFO logger isn't surfacing lifecycle events.
    """
    if os.environ.get("TRACE_TRANSLATION") == "1":
        try:
            print("[trace]", json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)
        except (TypeError, ValueError):
            print("[trace] (unencodable event)", file=sys.stderr, flush=True)


MARKER_ENABLE_RE = re.compile(
    r"\[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid=([a-fA-F0-9]{1,64})"
    r"(?::workspace=([A-Za-z0-9_\-]{1,64}))?"
    r"(?::workspace_name=([^\]]{1,128}))?\]"
)
MARKER_DISABLE_RE = re.compile(
    r"\[CC_I18N_PROXY:DISABLE_THIS_SESSION:uuid=([a-fA-F0-9]{1,64})\]"
)


def _scan_text(text: str) -> tuple[str, dict | None]:
    decision: dict | None = None
    stripped = text
    m_en = MARKER_ENABLE_RE.search(stripped)
    if m_en:
        decision = {
            "action": "enable",
            "uuid": m_en.group(1),
            "workspace_id": m_en.group(2) or "default",
            "workspace_name": m_en.group(3) or "default",
        }
        stripped = MARKER_ENABLE_RE.sub("", stripped)
    m_dis = MARKER_DISABLE_RE.search(stripped)
    if m_dis:
        decision = {"action": "disable", "uuid": m_dis.group(1)}
        stripped = MARKER_DISABLE_RE.sub("", stripped)
    return stripped, decision


def scan_and_strip_markers(body: dict) -> tuple[dict, dict | None]:
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body, None

    decision: dict | None = None
    new_msgs: list = []
    changed = False

    for msg in msgs:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            new_msgs.append(msg)
            continue
        content = msg.get("content")
        if isinstance(content, str):
            stripped, d = _scan_text(content)
            if d is not None:
                decision = d
            if stripped != content:
                new_msg = dict(msg)
                new_msg["content"] = stripped
                new_msgs.append(new_msg)
                changed = True
                continue
            new_msgs.append(msg)
            continue
        if isinstance(content, list):
            new_blocks: list = []
            block_changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    stripped, d = _scan_text(text)
                    if d is not None:
                        decision = d
                    if stripped != text:
                        new_block = dict(block)
                        new_block["text"] = stripped
                        new_blocks.append(new_block)
                        block_changed = True
                        continue
                new_blocks.append(block)
            if block_changed:
                new_msg = dict(msg)
                new_msg["content"] = new_blocks
                new_msgs.append(new_msg)
                changed = True
                continue
        new_msgs.append(msg)

    if not changed:
        return body, decision
    new_body = dict(body)
    new_body["messages"] = new_msgs
    return new_body, decision


def build_app(cfg: Config, *, pipeline: TranslationPipeline | None = None,
              chain: Translator | None = None,
              audit: AuditLogWriter | None = None,
              emitter: FileEmitter | None = None) -> FastAPI:
    app = FastAPI(title="cc-i18n-proxy")
    upstream_client = httpx.AsyncClient(base_url=cfg.anthropic_upstream, timeout=httpx.Timeout(120.0))

    state = {"pipeline": pipeline, "chain": chain, "audit": audit, "emitter": emitter, "cache": None,
             "state_store": None, "providers_cfg": None,
             "translation_sessions": {}}
    app.state.translation_sessions = state["translation_sessions"]

    @app.on_event("startup")
    async def _startup() -> None:
        if state["pipeline"] is None:
            from cc_i18n_proxy.providers import (
                StateStore, build_chain_from_config, load_providers_config, write_active_head,
            )
            cache = await TranslationCache.create(cfg.cache_db_path)
            toml_path = cfg.home / "providers.toml"
            env_path = cfg.home / ".env"
            state_path = cfg.home / "state.json"
            providers_cfg = load_providers_config(toml_path, dotenv_path=env_path)
            store = StateStore(state_path)
            if store.read_active_head() is None and providers_cfg.default_chain:
                write_active_head(state_path, providers_cfg.default_chain[0], updated_by="daemon_init")
            chain_built = build_chain_from_config(
                providers_cfg, active_head_reader=store.read_active_head,
            )
            state["cache"] = cache
            state["chain"] = chain_built
            state["state_store"] = store
            state["providers_cfg"] = providers_cfg
            state["pipeline"] = TranslationPipeline(translator=chain_built, cache=cache)
        if state["audit"] is None:
            state["audit"] = AuditLogWriter(cfg.audit_log_dir)
        if state["emitter"] is None:
            state["emitter"] = FileEmitter(cfg.emit_file_dir)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await upstream_client.aclose()
        if state["cache"] is not None:
            await state["cache"].close()

    @app.post("/v1/messages")
    async def messages(request: Request) -> StreamingResponse:
        raw_body = await request.json()
        if cfg.log_protocol_observations:
            _log_protocol(cfg, raw_body)

        body, marker_decision = scan_and_strip_markers(raw_body)

        if marker_decision is not None and marker_decision.get("uuid"):
            session_id = marker_decision["uuid"]
        else:
            session_id = _derive_session_id(body, request.headers)

        if marker_decision is not None:
            if marker_decision["action"] == "enable":
                state["translation_sessions"][session_id] = {
                    "workspace_id": marker_decision.get("workspace_id", "default"),
                    "workspace_name": marker_decision.get("workspace_name", "default"),
                }
                log.info("session %s entered translation mode via /intl marker", session_id)
                _trace({"event": "translation_mode_enter", "session_id": session_id,
                        "workspace_id": marker_decision.get("workspace_id", "default")})
                try:
                    write_last_enable(
                        cfg.home,
                        workspace_id=marker_decision.get("workspace_id", "default"),
                        session_id=session_id,
                    )
                except OSError as exc:
                    log.warning("failed to write last-enable sentinel: %s", exc)
            elif marker_decision["action"] == "disable":
                state["translation_sessions"].pop(session_id, None)
                log.info("session %s exited translation mode via /normal marker", session_id)
                _trace({"event": "translation_mode_exit", "session_id": session_id})

        translation_active = session_id in state["translation_sessions"]

        if translation_active:
            translation_status: dict[str, str] = {"user": "ok"}
            sources: dict[str, str] = {"user": "fallback_passthrough", "assistant": "translator_api"}
            user_provider = ""
            user_failover_attempts: list[str] = []
            user_failover_errors: list[dict] = []
            try:
                pipeline_result = await state["pipeline"].translate_request_body(body)
                translated_body = pipeline_result.body
                sources = pipeline_result.sources
                user_provider = pipeline_result.user_provider
                user_failover_attempts = pipeline_result.user_failover_attempts
                user_failover_errors = pipeline_result.user_failover_errors
                translation_status = {"user": pipeline_result.user_status}
                if user_failover_attempts:
                    _trace({"event": "chain_failover", "session_id": session_id, "direction": "user",
                            "attempts": user_failover_attempts, "errors": user_failover_errors})
                if pipeline_result.user_status == "translator_config_error":
                    _trace({"event": "translator_config_error", "session_id": session_id, "direction": "user"})
            except Exception as exc:
                log.exception("schema/pipeline failure; passthrough body")
                translated_body = body
                translation_status = {"user": "schema_parse_failed"}
                await state["emitter"].emit_warning(session_id, f"schema parse failed: {exc}")
        else:
            translated_body = body
            translation_status = {"user": "passthrough"}

        forward_headers = _strip_hop_headers(dict(request.headers))

        try:
            upstream = await upstream_client.post("/v1/messages", json=translated_body, headers=forward_headers)
        except httpx.RequestError as exc:
            return StreamingResponse(
                _byte_stream(json.dumps({"type": "error", "error": {"type": "upstream", "message": str(exc)}}).encode()),
                status_code=502,
                media_type="application/json",
            )

        content_type = upstream.headers.get("content-type", "application/json")
        upstream_bytes = upstream.content

        if translation_active:
            ws_info = state["translation_sessions"].get(
                session_id, {"workspace_id": "default", "workspace_name": "default"}
            )
            asyncio.create_task(_post_response_fork(
                cfg, state, session_id, body, translated_body, upstream_bytes,
                sources, translation_status,
                user_provider=user_provider,
                user_failover_attempts=user_failover_attempts,
                user_failover_errors=user_failover_errors,
                workspace_id=ws_info["workspace_id"],
                workspace_name=ws_info["workspace_name"],
            ))

        return StreamingResponse(
            _byte_stream(upstream_bytes),
            status_code=upstream.status_code,
            media_type=content_type,
        )

    @app.post("/v1/internal/retry")
    async def retry_turn(req: _RetryReq) -> dict:
        audit_dir = cfg.audit_log_dir
        safe_session = req.session
        if not _SAFE_SESSION_ID_RE.match(safe_session):
            raise HTTPException(400, "invalid session id")
        audit_path = audit_dir / f"{safe_session}.jsonl"
        if not audit_path.exists():
            raise HTTPException(404, "session not found")

        target = None
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("turn_id") == req.turn_id:
                target = entry
                break
        if target is None:
            raise HTTPException(404, "turn not found")

        status = target.get("translation_status", {})
        if isinstance(status, str):
            status = {"user": status, "assistant": status}
        if status.get("assistant") == "ok":
            raise HTTPException(400, "turn already succeeded; nothing to retry")

        head = req.head
        if not head:
            chain_obj = state["chain"]
            failed_provs = [
                err.get("provider") for err in target.get("failover_errors", {}).get("assistant", [])
            ]
            for named in chain_obj._default_chain:
                if named.name not in failed_provs:
                    head = named.name
                    break
            if not head:
                raise HTTPException(503, "all known providers already failed for this turn")

        chain_obj = state["chain"]
        assistant_en = target.get("assistant_en", "")
        new_status = {"user": status.get("user", "ok"), "assistant": "ok"}
        new_providers = {"user": target.get("translation_providers", {}).get("user", ""), "assistant": ""}
        new_attempts: dict[str, list[str]] = {"user": [], "assistant": []}
        new_errors: dict[str, list[dict]] = {"user": [], "assistant": []}
        new_assistant_zh = assistant_en

        if assistant_en:
            try:
                annotated = await chain_obj.translate_with_head(
                    assistant_en, source="en", target="zh", head_name=head,
                )
                new_assistant_zh = annotated.result.text
                new_providers["assistant"] = annotated.provider_name
                new_attempts["assistant"] = list(annotated.failover_attempts)
                new_errors["assistant"] = list(annotated.failover_errors)
            except TranslatorChainExhausted as exc:
                new_status["assistant"] = "translate_api_outage"
                new_errors["assistant"] = list(exc.errors)
            except TranslatorConfigError as exc:
                new_status["assistant"] = "translator_config_error"
                new_attempts["assistant"] = list(exc.failover_attempts)
                new_errors["assistant"] = list(exc.failover_errors)
            except Exception as exc:
                new_status["assistant"] = "translate_api_outage"
                new_errors["assistant"] = [{"provider": head, "message": str(exc)}]

        new_entry = TurnEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=safe_session,
            turn_id=int(datetime.now(timezone.utc).timestamp() * 1000) % 10_000_000,
            user_zh=target.get("user_zh", ""),
            user_en=target.get("user_en", ""),
            assistant_en=assistant_en,
            assistant_zh=new_assistant_zh,
            translation_sources=target.get("translation_sources", {}),
            tokens=target.get("tokens", {}),
            translation_status=new_status,
            translation_providers=new_providers,
            failover_attempts=new_attempts,
            failover_errors=new_errors,
            retry_of=req.turn_id,
            workspace_id=target.get("workspace_id", ""),
            workspace_name=target.get("workspace_name", ""),
        )
        await state["audit"].write(new_entry)
        return {
            "turn_id": new_entry.turn_id,
            "retry_of": new_entry.retry_of,
            "translation_status": new_entry.translation_status,
            "translation_providers": new_entry.translation_providers,
            "failover_attempts": new_entry.failover_attempts,
            "failover_errors": new_entry.failover_errors,
            "assistant_zh": new_entry.assistant_zh,
        }

    return app


async def _post_response_fork(
    cfg: Config, state: dict, session_id: str,
    original_body: dict, translated_body: dict,
    upstream_bytes: bytes,
    sources: dict[str, str],
    translation_status: dict[str, str],
    *,
    user_provider: str,
    user_failover_attempts: list[str],
    user_failover_errors: list[dict],
    workspace_id: str = "default",
    workspace_name: str = "default",
) -> None:
    """Translate assistant text → audit log + emitter. Fire-and-forget.

    Wrapped in a top-level try/except so any unhandled exception is logged
    instead of being silently dropped by asyncio (the task return value is
    discarded by the caller).
    """
    try:
        # Real Anthropic streams SSE; tests mock plain JSON. Try JSON first, fall back to SSE.
        try:
            upstream_json = json.loads(upstream_bytes)
            assistant_text_parts: list[str] = []
            if isinstance(upstream_json.get("content"), list):
                for block in upstream_json["content"]:
                    if block.get("type") == "text":
                        assistant_text_parts.append(block.get("text", ""))
            usage = upstream_json.get("usage", {})
        except json.JSONDecodeError:
            assistant_text_parts, usage = _parse_anthropic_sse(upstream_bytes)
            upstream_json = {"usage": usage}
        assistant_en = "".join(assistant_text_parts)

        assistant_provider = ""
        assistant_failover_attempts: list[str] = []
        assistant_failover_errors: list[dict] = []
        assistant_status = "ok"
        assistant_zh = ""

        if assistant_en:
            try:
                chain = state["chain"]
                annotated = await chain.translate(assistant_en, source="en", target="zh")
                assistant_zh = annotated.result.text
                assistant_provider = annotated.provider_name
                assistant_failover_attempts = list(annotated.failover_attempts)
                assistant_failover_errors = list(annotated.failover_errors)
                sources["assistant"] = "translator_api"
            except TranslatorChainExhausted as exc:
                assistant_zh = assistant_en
                sources["assistant"] = "fallback_passthrough"
                assistant_status = "translate_api_outage"
                assistant_failover_errors = list(exc.errors)
                translation_status["assistant"] = assistant_status
            except TranslatorConfigError as exc:
                assistant_zh = assistant_en
                sources["assistant"] = "fallback_passthrough"
                assistant_status = "translator_config_error"
                assistant_failover_attempts = list(exc.failover_attempts)
                assistant_failover_errors = list(exc.failover_errors)
                translation_status["assistant"] = assistant_status
            except Exception as exc:
                log.warning("assistant translation failed: %s", exc)
                assistant_zh = assistant_en
                sources["assistant"] = "fallback_passthrough"
                assistant_status = "translate_api_outage"
                translation_status["assistant"] = assistant_status

        if "assistant" not in translation_status:
            translation_status["assistant"] = assistant_status

        if assistant_failover_attempts:
            _trace({"event": "chain_failover", "session_id": session_id, "direction": "assistant",
                    "attempts": assistant_failover_attempts, "errors": assistant_failover_errors})
        if assistant_status == "translator_config_error":
            _trace({"event": "translator_config_error", "session_id": session_id, "direction": "assistant"})

        user_zh = _flatten_user_text(original_body)
        user_en = _flatten_user_text(translated_body)
        prompt_source = _classify_user_text(user_zh)

        entry = TurnEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            turn_id=int(datetime.now(timezone.utc).timestamp() * 1000) % 10_000_000,
            user_zh=user_zh,
            user_en=user_en,
            assistant_en=assistant_en,
            assistant_zh=assistant_zh,
            translation_sources=sources,
            tokens={
                "input_anthropic": upstream_json.get("usage", {}).get("input_tokens", 0),
                "output_anthropic": upstream_json.get("usage", {}).get("output_tokens", 0),
                "translator_api_calls": (1 if sources.get("assistant") == "translator_api" else 0)
                                        + (1 if sources.get("user") == "translator_api" else 0),
            },
            translation_status=translation_status,
            translation_providers={
                "user": user_provider,
                "assistant": assistant_provider,
            },
            failover_attempts={
                "user": list(user_failover_attempts),
                "assistant": list(assistant_failover_attempts),
            },
            failover_errors={
                "user": list(user_failover_errors),
                "assistant": list(assistant_failover_errors),
            },
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            prompt_source=prompt_source,
        )
        await state["audit"].write(entry)
        if assistant_zh and prompt_source not in {"recap", "hook", "command"}:
            quoted_user = _format_user_quote(user_zh)
            prefix = f"\n{quoted_user}\n\n" if quoted_user else "\n"
            await state["emitter"].emit(session_id, f"{prefix}{assistant_zh}\n\n---\n")
    except Exception as exc:  # noqa: BLE001
        log.exception("post-response fork failed for session %s: %s", session_id, exc)


def _parse_anthropic_sse(raw: bytes) -> tuple[list[str], dict[str, int]]:
    """Extract assistant text deltas and usage from an Anthropic SSE stream body.

    Anthropic event types we care about:
      - content_block_delta with delta.type == "text_delta": append delta.text
      - message_start: capture message.usage (input_tokens)
      - message_delta: capture usage.output_tokens
    """
    text_parts: list[str] = []
    usage: dict[str, int] = {}
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return text_parts, usage
    for chunk in text.split("\n\n"):
        data_line = next((ln for ln in chunk.splitlines() if ln.startswith("data: ")), None)
        if data_line is None:
            continue
        payload = data_line[len("data: "):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "content_block_delta":
            delta = evt.get("delta", {})
            if delta.get("type") == "text_delta":
                text_parts.append(delta.get("text", ""))
        elif etype == "message_start":
            msg_usage = evt.get("message", {}).get("usage", {})
            if "input_tokens" in msg_usage:
                usage["input_tokens"] = msg_usage["input_tokens"]
        elif etype == "message_delta":
            evt_usage = evt.get("usage", {})
            if "output_tokens" in evt_usage:
                usage["output_tokens"] = evt_usage["output_tokens"]
    return text_parts, usage


_RECAP_USER_PATTERNS = (
    "The user stepped away",
    "The user is coming back",
)


def _classify_user_text(user_zh: str) -> str:
    """Classify a user_zh string into prompt source category.

    Used to mark TurnEntry.prompt_source so emit/UI can hide system-injected
    turns (recap, hook) while keeping audit JSONL complete.

    Pattern match is fail-open: unknown text defaults to "human" so we never
    mistakenly hide real conversation.
    """
    if not user_zh:
        return "human"
    head = user_zh[:200]
    if any(p in head for p in _RECAP_USER_PATTERNS) or "Recap in under" in head:
        return "recap"
    if user_zh.startswith("Stop hook feedback:"):
        return "hook"
    if user_zh.startswith("<command-message>"):
        return "command"
    return "human"


def _format_user_quote(user_zh: str) -> str:
    """Format user_zh as a markdown blockquote prefixed with 👤.

    Each line gets `> ` prefix so multi-line prompts render as one blockquote.
    Empty input returns "" (caller skips the quote section).
    """
    if not user_zh:
        return ""
    lines = user_zh.split("\n")
    quoted = []
    for i, line in enumerate(lines):
        if i == 0:
            quoted.append(f"> 👤 {line}")
        elif line:
            quoted.append(f"> {line}")
        else:
            quoted.append(">")
    return "\n".join(quoted)


def _flatten_user_text(body: dict[str, Any]) -> str:
    msgs = body.get("messages", [])
    if not msgs:
        return ""
    last_user = next((m for m in reversed(msgs) if m.get("role") == "user"), None)
    if not last_user:
        return ""
    content = last_user.get("content", "")
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if block.get("type") == "text" and not block.get("text", "").startswith("<system-reminder>"):
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _derive_session_id(body: dict, headers) -> str:
    h = headers.get("x-cc-i18n-session") if hasattr(headers, "get") else None
    if h:
        return h
    # Fallback: hash first user message + model — stable per session
    import hashlib
    msgs = body.get("messages", [])
    if not isinstance(msgs, list):
        msgs = []
    first_user = next((m for m in msgs if isinstance(m, dict) and m.get("role") == "user"), None)
    fingerprint = json.dumps({"m": body.get("model"), "u": first_user}, ensure_ascii=False, default=str)
    return "sess-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]


async def _byte_stream(content: bytes) -> AsyncIterator[bytes]:
    yield content


def _strip_hop_headers(headers: dict[str, str]) -> dict[str, str]:
    drop = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}
    return {k: v for k, v in headers.items() if k.lower() not in drop}


def _log_protocol(cfg: Config, body: dict[str, Any]) -> None:
    cfg.protocol_observations_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with cfg.protocol_observations_path.open("a", encoding="utf-8") as fp:
        fp.write(f"\n## {ts}\n\n```json\n{json.dumps(body, ensure_ascii=False, indent=2)}\n```\n")


# Module-level app for `uv run python -m cc_i18n_proxy`
app = build_app(Config.from_env())
