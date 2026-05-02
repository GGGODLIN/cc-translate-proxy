"""Tests for the proxy's internal retry endpoint."""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from cc_i18n_proxy.audit import AuditLogWriter, TurnEntry
from cc_i18n_proxy.cache import TranslationCache
from cc_i18n_proxy.pipeline import TranslationPipeline
from cc_i18n_proxy.server import build_app
from cc_i18n_proxy.translator import (
    NamedAdapter, NetworkError, TranslationResult, TranslatorChain,
)


class _Fake:
    def __init__(self, name, behavior=None):
        self.name = name
        self.behavior = behavior

    async def translate(self, text, *, source, target):
        if callable(self.behavior):
            return self.behavior(text)
        return TranslationResult(text=f"{self.name}:{text}", source_lang=source, target_lang=target)


def _ok(name):
    return NamedAdapter(name=name, adapter=_Fake(name))


def _raises(name, exc):
    def b(t):
        raise exc
    return NamedAdapter(name=name, adapter=_Fake(name, behavior=b))


@pytest.fixture
def chain():
    a = _raises("a", NetworkError("dead"))
    b = _ok("b")
    return TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b},
        active_head_reader=lambda: None,
    )


@pytest.fixture
async def app_with_chain(tmp_config, chain, tmp_path):
    cache = await TranslationCache.create(tmp_path / "cache.db")
    pipeline = TranslationPipeline(translator=chain, cache=cache)
    audit = AuditLogWriter(tmp_config.audit_log_dir)
    app = build_app(tmp_config, pipeline=pipeline, chain=chain, audit=audit)
    yield app, audit
    await cache.close()


def _make_entry(session_id, turn_id, status_assistant="translate_api_outage"):
    return TurnEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        session_id=session_id, turn_id=turn_id,
        user_zh="原文", user_en="text",
        assistant_en="english assistant reply", assistant_zh="english assistant reply",
        translation_sources={"user": "cache", "assistant": "fallback_passthrough"},
        tokens={"input_anthropic": 1, "output_anthropic": 1, "translator_api_calls": 0},
        translation_status={"user": "ok", "assistant": status_assistant},
        translation_providers={"user": "", "assistant": ""},
        failover_attempts={"user": [], "assistant": ["a"]},
        failover_errors={"user": [], "assistant": [{"provider": "a", "code": "network"}]},
    )


@pytest.mark.asyncio
async def test_retry_appends_new_turn(app_with_chain):
    app, audit = app_with_chain
    session = "testsess123"
    await audit.write(_make_entry(session, turn_id=1))

    client = TestClient(app)
    resp = client.post("/v1/internal/retry", json={
        "session": session, "turn_id": 1, "head": "b",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["retry_of"] == 1
    assert data["translation_status"]["assistant"] == "ok"
    assert data["translation_providers"]["assistant"] == "b"

    audit_file = audit._dir / f"{session}.jsonl"
    lines = audit_file.read_text().strip().split("\n")
    assert len(lines) == 2
    new_entry = json.loads(lines[1])
    assert new_entry["retry_of"] == 1


@pytest.mark.asyncio
async def test_retry_404_when_turn_missing(app_with_chain):
    app, audit = app_with_chain
    session = "testsess123"
    await audit.write(_make_entry(session, turn_id=1))

    client = TestClient(app)
    resp = client.post("/v1/internal/retry", json={
        "session": session, "turn_id": 999, "head": "b",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_400_when_turn_already_succeeded(app_with_chain):
    app, audit = app_with_chain
    session = "testsess123"
    await audit.write(_make_entry(session, turn_id=1, status_assistant="ok"))

    client = TestClient(app)
    resp = client.post("/v1/internal/retry", json={
        "session": session, "turn_id": 1, "head": "b",
    })
    assert resp.status_code == 400
    assert "already succeeded" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_retry_uses_next_in_chain_when_head_empty(app_with_chain):
    """When head not given, retry uses the next provider after the one that failed."""
    app, audit = app_with_chain
    session = "testsess123"
    await audit.write(_make_entry(session, turn_id=1))  # failed_provider="a"

    client = TestClient(app)
    resp = client.post("/v1/internal/retry", json={"session": session, "turn_id": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["translation_providers"]["assistant"] == "b"
