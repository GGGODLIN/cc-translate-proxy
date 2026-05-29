"""Tests for PipelineResult dataclass and TranslationPipeline status mapping."""
import pytest

from cc_i18n_proxy.cache import TranslationCache
from cc_i18n_proxy.pipeline import PipelineResult, TranslationPipeline
from cc_i18n_proxy.translator import (
    AuthError,
    NamedAdapter,
    NetworkError,
    RateLimitError,
    TranslationResult,
    TranslatorChain,
)


class _FakeAdapter:
    def __init__(self, name, behavior):
        self.name = name
        self.behavior = behavior

    async def translate(self, text, *, source, target):
        return self.behavior(text)


def _adapter_returning(name, output_text):
    def b(text):
        return TranslationResult(text=output_text, source_lang="zh", target_lang="en")
    return NamedAdapter(name=name, adapter=_FakeAdapter(name, b))


def _adapter_raising(name, exc):
    def b(text):
        raise exc
    return NamedAdapter(name=name, adapter=_FakeAdapter(name, b))


@pytest.fixture
async def cache(tmp_path):
    c = await TranslationCache.create(tmp_path / "cache.db")
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_ok_status_when_translation_succeeds(cache):
    a = _adapter_returning("a", "Hello")
    chain = TranslatorChain(
        default_chain=[a], enabled_by_name={"a": a}, active_head_reader=lambda: None,
    )
    pipeline = TranslationPipeline(translator=chain, cache=cache)
    body = {"messages": [{"role": "user", "content": "你好"}]}
    result = await pipeline.translate_request_body(body)
    assert isinstance(result, PipelineResult)
    assert result.user_status == "ok"
    assert result.user_provider == "a"
    assert result.user_failover_attempts == []
    assert "Hello" in str(result.body)


@pytest.mark.asyncio
async def test_translate_api_outage_when_chain_exhausted(cache):
    a = _adapter_raising("a", RateLimitError("429"))
    b = _adapter_raising("b", NetworkError("timeout"))
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=lambda: None,
    )
    pipeline = TranslationPipeline(translator=chain, cache=cache)
    body = {"messages": [{"role": "user", "content": "你好"}]}
    result = await pipeline.translate_request_body(body)
    assert result.user_status == "translate_api_outage"
    assert result.user_provider == ""
    assert len(result.user_failover_errors) == 2
    assert "你好" in str(result.body)


@pytest.mark.asyncio
async def test_translator_config_error_status(cache):
    a = _adapter_raising("a", AuthError("401"))
    chain = TranslatorChain(
        default_chain=[a], enabled_by_name={"a": a}, active_head_reader=lambda: None,
    )
    pipeline = TranslationPipeline(translator=chain, cache=cache)
    body = {"messages": [{"role": "user", "content": "你好"}]}
    result = await pipeline.translate_request_body(body)
    assert result.user_status == "translator_config_error"
    assert any(e.get("provider") == "a" for e in result.user_failover_errors)
    assert "你好" in str(result.body)


@pytest.mark.asyncio
async def test_no_cjk_input_status_is_ok(cache):
    """When input has no CJK, no translation needed.

    Status must be `ok` (not `fallback_passthrough`). `fallback_passthrough` is
    reserved for "we tried but failed and kept the original" — distinct from
    "we never needed to translate."
    """
    a = _adapter_returning("a", "should-not-be-called")
    chain = TranslatorChain(
        default_chain=[a], enabled_by_name={"a": a}, active_head_reader=lambda: None,
    )
    pipeline = TranslationPipeline(translator=chain, cache=cache)
    body = {"messages": [{"role": "user", "content": "Plain English"}]}
    result = await pipeline.translate_request_body(body)
    assert result.user_status == "ok"
    assert result.user_provider == ""
