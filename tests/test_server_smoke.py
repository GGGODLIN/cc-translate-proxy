"""Smoke test: server starts and forwards request through full pipeline."""
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
from cc_i18n_proxy.translator import NamedAdapter, TranslationResult, TranslatorChain

_FAKE_UUID = "deadbeef0000"
_ENABLE = f"[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid={_FAKE_UUID}]"


@pytest.fixture
async def app_client(tmp_path, tmp_config):
    cache = await TranslationCache.create(tmp_path / "cache.db")
    translator = AsyncMock()
    translator.translate.side_effect = lambda text, source, target: TranslationResult(
        text="Hello" if source == "zh" else "你好",
        source_lang=source,
        target_lang=target,
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
    yield TestClient(app), translator
    await cache.close()


@respx.mock
def test_server_translates_user_and_forwards(app_client):
    client, translator = app_client
    forward = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi there"}],
            "model": "claude-opus-4-7",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
    )
    body = {
        "model": "claude-opus-4-7",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": f"{_ENABLE}你好"}],
    }
    resp = client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    assert resp.status_code == 200
    sent = json.loads(forward.calls[0].request.content)
    assert sent["messages"][0]["content"] == "Hello"


@respx.mock
def test_cache_hit_no_translator_call_on_repeat(app_client):
    client, translator = app_client
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 5, "output_tokens": 2},
        })
    )
    body = {"model": "x", "max_tokens": 1, "messages": [{"role": "user", "content": f"{_ENABLE}你好"}]}

    client.post("/v1/messages", json=body, headers={"x-api-key": "test"})
    client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    user_translate_calls = sum(
        1 for call in translator.translate.await_args_list
        if call.kwargs.get("source") == "zh"
    )
    assert user_translate_calls == 1, "second call should hit cache, not translate user msg again"


@respx.mock
def test_resume_with_50_turn_history_zero_user_translation(app_client):
    """Simulate /resume: same history sent twice. All user msgs cache-hit."""
    client, translator = app_client
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "m", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "ok"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    history = [
        {"role": "user", "content": f"{_ENABLE}問題 0" if i == 0 else f"問題 {i}"}
        for i in range(50)
    ]
    body = {"model": "x", "max_tokens": 1, "messages": history}

    client.post("/v1/messages", json=body, headers={"x-api-key": "test"})
    first_user_calls = sum(1 for c in translator.translate.await_args_list if c.kwargs.get("source") == "zh")
    assert first_user_calls == 50

    translator.translate.reset_mock()
    client.post("/v1/messages", json=body, headers={"x-api-key": "test"})
    second_user_calls = sum(1 for c in translator.translate.await_args_list if c.kwargs.get("source") == "zh")
    assert second_user_calls == 0, "/resume should yield 100% cache hit on user messages"
