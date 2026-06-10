"""Tier (f): emit md skips recap/hook turns; audit JSONL still records them."""
import json
import time
from unittest.mock import AsyncMock

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from cc_i18n_proxy.audit import AuditLogWriter
from cc_i18n_proxy.cache import TranslationCache
from cc_i18n_proxy.emitter import FileEmitter
from cc_i18n_proxy.pipeline import TranslationPipeline
from cc_i18n_proxy.server import build_app
from cc_i18n_proxy.translator import (
    NamedAdapter,
    TranslationResult,
    TranslatorChain,
)


@pytest.fixture
async def setup_app(tmp_path, tmp_config):
    cache = await TranslationCache.create(tmp_path / "cache.db")
    translator = AsyncMock()
    translator.translate.return_value = TranslationResult(
        text="translated", source_lang="zh", target_lang="en",
    )
    named = NamedAdapter(name="mock", adapter=translator)
    chain = TranslatorChain(
        default_chain=[named],
        enabled_by_name={"mock": named},
        active_head_reader=lambda: None,
    )
    pipeline = TranslationPipeline(translator=chain, cache=cache)
    audit = AuditLogWriter(tmp_path / "audit")
    emitter = FileEmitter(tmp_path / "emit")
    app = build_app(tmp_config, pipeline=pipeline, chain=chain, audit=audit, emitter=emitter)
    with TestClient(app) as client:
        yield client, tmp_path
    await cache.close()


_FAKE_UUID = "deadbeef0002"
_ENABLE = f"[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid={_FAKE_UUID}]"


def _anthropic_ok(text: str = "assistant English") -> Response:
    return Response(200, json={
        "id": "m", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": text}], "model": "x",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


def _post(client, content):
    body = {"model": "x", "max_tokens": 1,
            "messages": [{"role": "user", "content": f"{_ENABLE}{content}"}]}
    return client.post("/v1/messages", json=body, headers={"x-api-key": "test"})


def _emit_path(tmp_path, sid: str):
    return tmp_path / "emit" / f"cc-i18n-{sid}.md"


def _audit_path(tmp_path, sid: str):
    return tmp_path / "audit" / f"{sid}.jsonl"


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """Poll until predicate() is true. The post-response fork runs as a
    fire-and-forget task, so file assertions need a sync point."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _wait_audit_written(tmp_path, sid: str):
    path = _audit_path(tmp_path, sid)
    assert _wait_for(
        lambda: path.exists() and path.read_text(encoding="utf-8").endswith("\n")
    ), "post-response fork must write audit JSONL"
    return path


@respx.mock
def test_recap_turn_not_in_emit_md_but_in_audit(setup_app):
    client, tmp_path = setup_app
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_anthropic_ok("You are testing recap behavior."),
    )
    resp = _post(
        client,
        "The user stepped away and is coming back. Recap in under 40 words.",
    )
    assert resp.status_code == 200

    emit = _emit_path(tmp_path, _FAKE_UUID)
    if emit.exists():
        assert "You are testing recap" not in emit.read_text(encoding="utf-8")
        assert "translated" not in emit.read_text(encoding="utf-8") or \
            emit.read_text(encoding="utf-8").strip() == ""

    audit_lines = _wait_audit_written(tmp_path, _FAKE_UUID).read_text(
        encoding="utf-8"
    ).splitlines()
    assert audit_lines, "audit must still record recap turn"
    last = json.loads(audit_lines[-1])
    assert last["prompt_source"] == "recap"


@respx.mock
def test_hook_feedback_turn_not_in_emit(setup_app):
    client, tmp_path = setup_app
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_anthropic_ok("skip"),
    )
    resp = _post(client, "Stop hook feedback:\n[checkpoint] 是?")
    assert resp.status_code == 200

    emit = _emit_path(tmp_path, _FAKE_UUID)
    if emit.exists():
        text = emit.read_text(encoding="utf-8")
        assert "skip" not in text or text.strip() == ""

    audit = _wait_audit_written(tmp_path, _FAKE_UUID).read_text(encoding="utf-8")
    last = json.loads(audit.splitlines()[-1])
    assert last["prompt_source"] == "hook"


@respx.mock
def test_human_turn_emitted_normally(setup_app):
    client, tmp_path = setup_app
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_anthropic_ok("Hello there"),
    )
    resp = _post(client, "你好世界這是真人 prompt")
    assert resp.status_code == 200

    emit = _emit_path(tmp_path, _FAKE_UUID)
    assert _wait_for(
        lambda: emit.exists() and "translated" in emit.read_text(encoding="utf-8")
    ), "human turn must produce emit md"
    text = emit.read_text(encoding="utf-8")
    # blockquote `> 👤` from Tier (e)
    assert "👤" in text
    # assistant translated content
    assert "translated" in text

    audit = _wait_audit_written(tmp_path, _FAKE_UUID).read_text(encoding="utf-8")
    last = json.loads(audit.splitlines()[-1])
    assert last["prompt_source"] == "human"


@respx.mock
def test_command_message_not_in_emit_md_but_in_audit(setup_app):
    """Slash command expansions are system-injected — skip emit, keep audit."""
    client, tmp_path = setup_app
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_anthropic_ok("Translation mode ready"),
    )
    resp = _post(client, "<command-message>intl</command-message>")
    assert resp.status_code == 200

    emit = _emit_path(tmp_path, _FAKE_UUID)
    if emit.exists():
        text = emit.read_text(encoding="utf-8")
        assert "Translation mode ready" not in text or text.strip() == ""
        assert "command-message" not in text or text.strip() == ""

    audit = _wait_audit_written(tmp_path, _FAKE_UUID).read_text(encoding="utf-8")
    last = json.loads(audit.splitlines()[-1])
    assert last["prompt_source"] == "command"
