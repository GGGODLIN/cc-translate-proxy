"""Grace degrade: translator outage / schema fail / upstream error."""
import json
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
from cc_i18n_proxy.translator import NamedAdapter, TranslatorChain

_FAKE_UUID = "deadbeef0001"
_ENABLE = f"[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid={_FAKE_UUID}]"


@pytest.fixture
async def setup_app(tmp_path, tmp_config):
    cache = await TranslationCache.create(tmp_path / "cache.db")
    translator = AsyncMock()
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
    yield TestClient(app), translator, tmp_path
    await cache.close()


@respx.mock
def test_translator_outage_passes_through_chinese_to_anthropic(setup_app):
    client, translator, _ = setup_app
    translator.translate.side_effect = RuntimeError("Gemini timeout")
    forward = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "m", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "ok"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    body = {"model": "x", "max_tokens": 1, "messages": [{"role": "user", "content": f"{_ENABLE}你好"}]}
    resp = client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    assert resp.status_code == 200
    sent = json.loads(forward.calls[0].request.content)
    # Grace degrade: original Chinese passes through to Anthropic.
    assert sent["messages"][0]["content"] == "你好"


@respx.mock
def test_anthropic_5xx_passes_through_to_cc(setup_app):
    client, translator, _ = setup_app
    from cc_i18n_proxy.translator import TranslationResult
    translator.translate.return_value = TranslationResult(text="Hello", source_lang="zh", target_lang="en")
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(503, json={"type": "error", "error": {"type": "overloaded"}})
    )
    body = {"model": "x", "max_tokens": 1, "messages": [{"role": "user", "content": "你好"}]}
    resp = client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    assert resp.status_code == 503
    payload = resp.json()
    assert payload["error"]["type"] == "overloaded"


@respx.mock
def test_malformed_messages_passes_through_with_warning(setup_app):
    client, translator, tmp_path = setup_app
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "m", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "ok"}], "model": "x",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })
    )
    # malformed: messages is a string instead of list
    body = {"model": "x", "max_tokens": 1, "messages": "this is wrong"}
    resp = client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    # Even on malformed body, proxy should not 500 — it should degrade gracefully.
    assert resp.status_code == 200
