"""Tests for /intl marker scan/strip helper + dispatch integration."""
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
from cc_i18n_proxy.server import (
    build_app,
    scan_and_strip_markers,
)
from cc_i18n_proxy.translator import NamedAdapter, TranslationResult, TranslatorChain


_FAKE_UUID = "a3f9bd5e1e7c"
_ENABLE = f"[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid={_FAKE_UUID}]"
_DISABLE = f"[CC_I18N_PROXY:DISABLE_THIS_SESSION:uuid={_FAKE_UUID}]"


def test_no_marker_returns_unchanged_body_and_none_decision():
    body = {"messages": [{"role": "user", "content": "你好"}]}
    out_body, decision = scan_and_strip_markers(body)
    assert out_body == body
    assert decision is None


def test_enable_marker_with_uuid_strips_and_returns_enable_dict():
    marker = "[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid=a3f9bd5e1e7c]"
    body = {
        "messages": [
            {"role": "user", "content": f"{marker}你好"},
        ],
    }
    out_body, decision = scan_and_strip_markers(body)
    assert out_body["messages"][0]["content"] == "你好"
    assert decision == {
        "action": "enable", "uuid": "a3f9bd5e1e7c",
        "workspace_id": "default", "workspace_name": "default",
    }


def test_disable_marker_with_uuid_in_block_content_strips_and_returns_disable_dict():
    marker = "[CC_I18N_PROXY:DISABLE_THIS_SESSION:uuid=a3f9bd5e1e7c]"
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{marker} bye"},
                ],
            },
        ],
    }
    out_body, decision = scan_and_strip_markers(body)
    assert out_body["messages"][0]["content"][0]["text"] == " bye"
    assert decision == {"action": "disable", "uuid": "a3f9bd5e1e7c"}


def test_marker_only_in_assistant_message_is_ignored():
    marker = "[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid=a3f9bd5e1e7c]"
    body = {
        "messages": [
            {"role": "assistant", "content": f"{marker} echoed back"},
            {"role": "user", "content": "問題"},
        ],
    }
    out_body, decision = scan_and_strip_markers(body)
    assert out_body == body
    assert decision is None


@pytest.fixture
async def passthrough_client(tmp_path, tmp_config):
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
    yield TestClient(app), translator, app
    await cache.close()


@respx.mock
def test_request_without_marker_passes_through_unchanged(passthrough_client):
    client, translator, _ = passthrough_client
    forward = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    body = {"model": "x", "max_tokens": 1, "messages": [{"role": "user", "content": "你好"}]}
    resp = client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    assert resp.status_code == 200
    sent = json.loads(forward.calls[0].request.content)
    assert sent["messages"][0]["content"] == "你好", "passthrough should not translate"
    assert translator.translate.await_count == 0, "translator must not be called in passthrough mode"


@respx.mock
def test_enable_marker_uuid_overrides_session_id_and_translates(passthrough_client):
    client, translator, app = passthrough_client
    forward = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    body_enable = {
        "model": "x", "max_tokens": 1,
        "messages": [{"role": "user", "content": f"{_ENABLE}你好"}],
    }
    client.post("/v1/messages", json=body_enable, headers={"x-api-key": "test"})

    sent_first = json.loads(forward.calls[0].request.content)
    assert _ENABLE not in json.dumps(sent_first), "marker must be stripped before forwarding"
    assert sent_first["messages"][0]["content"] == "Hello", "translation should happen on enable request"
    assert _FAKE_UUID in app.state.translation_sessions, \
        "white-list should now contain the UUID from the marker"

    body_followup = {
        "model": "x", "max_tokens": 1,
        "messages": [{"role": "user", "content": "你好"}],
    }
    client.post("/v1/messages", json=body_followup, headers={"x-api-key": "test"})
    sent_second = json.loads(forward.calls[1].request.content)
    assert sent_second["messages"][0]["content"] == "你好", \
        "single-message body without marker derives a different session_id → passthrough (not whitelisted)"


@respx.mock
def test_disable_marker_with_matching_uuid_removes_session(passthrough_client):
    client, translator, app = passthrough_client
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    enable = {"model": "x", "max_tokens": 1,
              "messages": [{"role": "user", "content": f"{_ENABLE}你好"}]}
    client.post("/v1/messages", json=enable, headers={"x-api-key": "test"})
    assert _FAKE_UUID in app.state.translation_sessions

    disable = {"model": "x", "max_tokens": 1,
               "messages": [{"role": "user", "content": f"{_DISABLE}你好"}]}
    client.post("/v1/messages", json=disable, headers={"x-api-key": "test"})
    assert _FAKE_UUID not in app.state.translation_sessions, \
        "white-list should be empty after /normal"


@respx.mock
def test_marker_in_history_keeps_session_whitelisted_on_followup_post(passthrough_client):
    """Real CC flow: CC re-sends the original /intl message every subsequent turn
    (it's part of the conversation history). The historical marker re-enables the
    session each turn, so a brand-new user message in the body still translates."""
    client, translator, app = passthrough_client
    forward = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    body_first = {"model": "x", "max_tokens": 1,
                  "messages": [{"role": "user", "content": f"{_ENABLE}你好"}]}
    client.post("/v1/messages", json=body_first, headers={"x-api-key": "test"})

    body_followup = {
        "model": "x", "max_tokens": 1,
        "messages": [
            {"role": "user", "content": f"{_ENABLE}你好"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "再說一次"},
        ],
    }
    client.post("/v1/messages", json=body_followup, headers={"x-api-key": "test"})

    sent = json.loads(forward.calls[1].request.content)
    assert _ENABLE not in json.dumps(sent), "historical marker still gets stripped on every forward"
    assert sent["messages"][2]["content"] == "Hello", \
        "brand-new user message translates because historical marker re-enabled the session"


@respx.mock
def test_disable_after_enable_in_history_yields_passthrough(passthrough_client):
    """Conversation history contains both /intl and /normal markers (user enabled
    then disabled mid-conversation). _scan_text's 'disable wins' rule must apply,
    so the brand-new user message after both markers is passthrough."""
    client, translator, app = passthrough_client
    forward = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    body = {
        "model": "x", "max_tokens": 1,
        "messages": [
            {"role": "user", "content": f"{_ENABLE}你好"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": f"{_DISABLE}結束"},
            {"role": "assistant", "content": "Bye"},
            {"role": "user", "content": "再見"},
        ],
    }
    client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    sent = json.loads(forward.calls[0].request.content)
    assert sent["messages"][4]["content"] == "再見", \
        "after disable wins, brand-new user message stays untranslated"
    assert _FAKE_UUID not in app.state.translation_sessions, \
        "white-list cleared by disable marker"


# === Tier (d): workspace segments ===

def test_marker_with_workspace_segments():
    text = f"prefix [CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid={_FAKE_UUID}:workspace=W1:workspace_name=Test Project] suffix"
    body = {"messages": [{"role": "user", "content": text}]}
    out_body, decision = scan_and_strip_markers(body)
    assert decision == {
        "action": "enable", "uuid": _FAKE_UUID,
        "workspace_id": "W1", "workspace_name": "Test Project",
    }
    assert "[CC_I18N_PROXY" not in out_body["messages"][0]["content"]


def test_marker_without_workspace_defaults():
    text = _ENABLE
    body = {"messages": [{"role": "user", "content": text}]}
    _, decision = scan_and_strip_markers(body)
    assert decision["uuid"] == _FAKE_UUID
    assert decision["workspace_id"] == "default"
    assert decision["workspace_name"] == "default"


def test_marker_with_only_workspace_id():
    text = f"[CC_I18N_PROXY:ENABLE_THIS_SESSION:uuid={_FAKE_UUID}:workspace=W2]"
    body = {"messages": [{"role": "user", "content": text}]}
    _, decision = scan_and_strip_markers(body)
    assert decision["workspace_id"] == "W2"
    assert decision["workspace_name"] == "default"


@respx.mock
def test_enable_marker_writes_last_enable_sentinel(passthrough_client, tmp_config):
    from cc_i18n_proxy.intl_sentinel import read_last_enable

    client, _translator, _app = passthrough_client
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}], "model": "x",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })
    )
    body = {
        "model": "x", "max_tokens": 1,
        "messages": [{"role": "user", "content": f"{_ENABLE}你好"}],
    }
    client.post("/v1/messages", json=body, headers={"x-api-key": "test"})

    sentinel = read_last_enable(tmp_config.home, workspace_id="default")
    assert sentinel is not None, "enable marker must write the workspace sentinel"
    assert sentinel["session_id"] == _FAKE_UUID
    assert sentinel["workspace_id"] == "default"
    assert sentinel["ts"] > 0

    other = read_last_enable(tmp_config.home, workspace_id="other")
    assert other is None, "unrelated workspace must not see this enable event"
