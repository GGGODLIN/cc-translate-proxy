"""Test Translator interface and GeminiNativeAdapter."""
from unittest.mock import AsyncMock, patch

import pytest

from cc_i18n_proxy.translator import (
    TranslationResult,
    has_cjk,
    GeminiNativeAdapter,
    _is_system_instruction_rejection,
)


class _FakeAPIError(Exception):
    """Mimics google.genai.errors.APIError: carries .code and .message."""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def test_has_cjk_detects_chinese():
    assert has_cjk("你好") is True
    assert has_cjk("hello world") is False
    assert has_cjk("hello 你好") is True
    assert has_cjk("") is False


@pytest.mark.asyncio
async def test_gemini_adapter_calls_api_and_returns_translation():
    adapter = GeminiNativeAdapter(api_key="fake-key")
    fake_resp = AsyncMock()
    fake_resp.text = "Hello, this is a test."

    with patch.object(adapter, "_generate_async", return_value=fake_resp) as mock_gen:
        result = await adapter.translate("你好，這是測試。", source="zh", target="en")

    assert isinstance(result, TranslationResult)
    assert result.text == "Hello, this is a test."
    assert result.source_lang == "zh"
    assert result.target_lang == "en"
    mock_gen.assert_awaited_once()


@pytest.mark.asyncio
async def test_gemini_adapter_raises_on_empty_response():
    adapter = GeminiNativeAdapter(api_key="fake-key")
    fake_resp = AsyncMock()
    fake_resp.text = ""

    with patch.object(adapter, "_generate_async", return_value=fake_resp):
        with pytest.raises(RuntimeError, match="empty"):
            await adapter.translate("你好", source="zh", target="en")


def test_is_system_instruction_rejection_detection():
    assert _is_system_instruction_rejection(
        _FakeAPIError(400, "System instruction is not supported")) is True
    assert _is_system_instruction_rejection(
        _FakeAPIError(400, "Developer instruction is not enabled for models/gemma-3")) is True
    assert _is_system_instruction_rejection(
        _FakeAPIError(400, "some other bad request")) is False
    assert _is_system_instruction_rejection(
        _FakeAPIError(429, "system instruction")) is False
    assert _is_system_instruction_rejection(RuntimeError("system instruction")) is False


@pytest.mark.asyncio
async def test_gemini_falls_back_to_concat_when_system_instruction_rejected():
    adapter = GeminiNativeAdapter(api_key="fake-key")
    good = AsyncMock()
    good.text = "translated"
    reject = _FakeAPIError(400, "Developer instruction is not enabled for models/gemma-3")

    with patch.object(adapter, "_generate_async", side_effect=[reject, good]) as mock_gen:
        result = await adapter.translate("你好", source="zh", target="en")

    assert result.text == "translated"
    assert mock_gen.await_count == 2
    assert mock_gen.await_args_list[0].kwargs["use_system_instruction"] is True
    assert mock_gen.await_args_list[1].kwargs["use_system_instruction"] is False


@pytest.mark.asyncio
async def test_gemini_caches_fallback_decision_after_rejection():
    adapter = GeminiNativeAdapter(api_key="fake-key")
    good = AsyncMock()
    good.text = "ok"
    reject = _FakeAPIError(400, "system_instruction is not supported")

    with patch.object(adapter, "_generate_async", side_effect=[reject, good, good]) as mock_gen:
        await adapter.translate("一", source="zh", target="en")
        await adapter.translate("二", source="zh", target="en")

    assert mock_gen.await_count == 3
    assert mock_gen.await_args_list[2].kwargs["use_system_instruction"] is False


@pytest.mark.asyncio
async def test_gemini_propagates_non_system_instruction_error():
    adapter = GeminiNativeAdapter(api_key="fake-key")
    boom = _FakeAPIError(429, "rate limited")

    with patch.object(adapter, "_generate_async", side_effect=boom):
        with pytest.raises(_FakeAPIError):
            await adapter.translate("你好", source="zh", target="en")
