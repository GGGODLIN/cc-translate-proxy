"""Tests for TranslatorChain — ordering, reactive failover, fatal short-circuit."""
import pytest

from cc_i18n_proxy.translator import (
    AuthError,
    ClientError,
    NamedAdapter,
    NetworkError,
    RateLimitError,
    ServerError,
    TranslationResult,
    TranslatorChain,
    TranslatorChainExhausted,
    TranslatorConfigError,
)


class _Fake:
    """Test double for an adapter. Configurable behavior per call."""

    def __init__(self, name: str, behavior=None):
        self.name = name
        self.calls: list[tuple[str, str, str]] = []
        self.behavior = behavior  # callable(text, source, target) -> TranslationResult OR raises

    async def translate(self, text, *, source, target):
        self.calls.append((text, source, target))
        if callable(self.behavior):
            return self.behavior(text, source=source, target=target)
        return TranslationResult(text=f"{self.name}:{text}", source_lang=source, target_lang=target)


def _ok(name):
    return NamedAdapter(name=name, adapter=_Fake(name))


def _raises(name, exc):
    def _b(text, **_):
        raise exc
    return NamedAdapter(name=name, adapter=_Fake(name, behavior=_b))


@pytest.fixture
def head_reader():
    """Stateful head reader; tests can mutate the holder."""
    holder = {"head": None}
    def _read():
        return holder["head"]
    _read.holder = holder
    return _read


# === Ordering ===

@pytest.mark.asyncio
async def test_ordering_head_in_default_chain(head_reader):
    a, b, c = _ok("a"), _ok("b"), _ok("c")
    chain = TranslatorChain(
        default_chain=[a, b, c],
        enabled_by_name={"a": a, "b": b, "c": c},
        active_head_reader=head_reader,
    )
    head_reader.holder["head"] = "b"
    annotated = await chain.translate("hi", source="en", target="zh")
    # b is head, then a and c follow (no duplicates)
    assert annotated.provider_name == "b"
    assert b.adapter.calls and not a.adapter.calls and not c.adapter.calls


@pytest.mark.asyncio
async def test_ordering_head_off_chain_but_enabled(head_reader):
    a, b = _ok("a"), _ok("b")
    deepseek = _ok("deepseek")
    chain = TranslatorChain(
        default_chain=[a, b],
        enabled_by_name={"a": a, "b": b, "deepseek": deepseek},
        active_head_reader=head_reader,
    )
    head_reader.holder["head"] = "deepseek"
    annotated = await chain.translate("hi", source="en", target="zh")
    assert annotated.provider_name == "deepseek"
    # deepseek tried first; a and b not yet (deepseek succeeded)
    assert deepseek.adapter.calls and not a.adapter.calls


@pytest.mark.asyncio
async def test_ordering_head_not_in_enabled_falls_back(head_reader, caplog):
    import logging
    a, b = _ok("a"), _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b],
        enabled_by_name={"a": a, "b": b},
        active_head_reader=head_reader,
    )
    head_reader.holder["head"] = "ghost"  # not in enabled
    with caplog.at_level(logging.WARNING, logger="cc_i18n_proxy.translator"):
        annotated = await chain.translate("hi", source="en", target="zh")
    assert annotated.provider_name == "a"  # falls back to default_chain[0]
    assert "ghost" in caplog.text and "not in enabled providers" in caplog.text


@pytest.mark.asyncio
async def test_ordering_no_head_uses_default_chain(head_reader):
    a, b = _ok("a"), _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    head_reader.holder["head"] = None
    annotated = await chain.translate("hi", source="en", target="zh")
    assert annotated.provider_name == "a"


# === Reactive failover ===

@pytest.mark.asyncio
async def test_failover_429_then_success(head_reader):
    a = _raises("a", RateLimitError("429"))
    b = _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    annotated = await chain.translate("hi", source="en", target="zh")
    assert annotated.provider_name == "b"
    assert annotated.failover_attempts == ["a"]
    assert len(annotated.failover_errors) == 1
    assert annotated.failover_errors[0]["provider"] == "a"


@pytest.mark.asyncio
async def test_failover_5xx(head_reader):
    a = _raises("a", ServerError("500"))
    b = _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    annotated = await chain.translate("hi", source="en", target="zh")
    assert annotated.provider_name == "b"


@pytest.mark.asyncio
async def test_failover_network_error(head_reader):
    a = _raises("a", NetworkError("timeout"))
    b = _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    annotated = await chain.translate("hi", source="en", target="zh")
    assert annotated.provider_name == "b"


@pytest.mark.asyncio
async def test_all_retryable_exhausted(head_reader):
    a = _raises("a", RateLimitError("429"))
    b = _raises("b", ServerError("500"))
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    with pytest.raises(TranslatorChainExhausted) as exc_info:
        await chain.translate("hi", source="en", target="zh")
    assert len(exc_info.value.errors) == 2


# === Fatal short-circuit ===

@pytest.mark.asyncio
async def test_auth_error_does_not_advance(head_reader):
    a = _raises("a", AuthError("401"))
    b = _ok("b")  # would succeed but should NOT be tried
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    with pytest.raises(TranslatorConfigError) as exc_info:
        await chain.translate("hi", source="en", target="zh")
    assert exc_info.value.provider == "a"
    assert isinstance(exc_info.value.cause, AuthError)
    assert not b.adapter.calls  # CRITICAL: did not try next


@pytest.mark.asyncio
async def test_client_error_does_not_advance(head_reader):
    a = _raises("a", ClientError("400"))
    b = _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b}, active_head_reader=head_reader,
    )
    with pytest.raises(TranslatorConfigError):
        await chain.translate("hi", source="en", target="zh")
    assert not b.adapter.calls


@pytest.mark.asyncio
async def test_fatal_after_retryable_records_history(head_reader):
    """Retryable errors before a fatal one should still appear in failover_attempts/errors."""
    a = _raises("a", RateLimitError("429"))
    b = _raises("b", AuthError("401"))
    c = _ok("c")
    chain = TranslatorChain(
        default_chain=[a, b, c], enabled_by_name={"a": a, "b": b, "c": c},
        active_head_reader=head_reader,
    )
    with pytest.raises(TranslatorConfigError) as exc_info:
        await chain.translate("hi", source="en", target="zh")
    assert exc_info.value.provider == "b"
    assert exc_info.value.failover_attempts == ["a"]
    # failover_errors carries retryable history AND the fatal entry itself
    assert len(exc_info.value.failover_errors) == 2
    assert exc_info.value.failover_errors[0]["provider"] == "a"
    assert exc_info.value.failover_errors[1]["provider"] == "b"
    assert not c.adapter.calls


@pytest.mark.asyncio
async def test_translate_with_head_explicit(head_reader):
    a, b = _ok("a"), _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b},
        active_head_reader=head_reader,
    )
    annotated = await chain.translate_with_head("hi", source="zh", target="en", head_name="b")
    assert annotated.provider_name == "b"


@pytest.mark.asyncio
async def test_translate_with_head_unknown_raises(head_reader):
    a = _ok("a")
    chain = TranslatorChain(
        default_chain=[a], enabled_by_name={"a": a}, active_head_reader=head_reader,
    )
    with pytest.raises(ValueError, match="not in enabled"):
        await chain.translate_with_head("hi", source="zh", target="en", head_name="ghost")


@pytest.mark.asyncio
async def test_translate_with_head_falls_through(head_reader):
    a = _raises("a", RateLimitError("429"))
    b = _ok("b")
    chain = TranslatorChain(
        default_chain=[a, b], enabled_by_name={"a": a, "b": b},
        active_head_reader=head_reader,
    )
    annotated = await chain.translate_with_head("hi", source="zh", target="en", head_name="a")
    assert annotated.provider_name == "b"
    assert annotated.failover_attempts == ["a"]
