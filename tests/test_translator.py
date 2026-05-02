"""Test Translator interface and GeminiFlashAdapter."""
from unittest.mock import AsyncMock, patch

import pytest

from cc_i18n_proxy.translator import (
    TranslationResult,
    Translator,
    has_cjk,
    GeminiFlashAdapter,
)


def test_has_cjk_detects_chinese():
    assert has_cjk("你好") is True
    assert has_cjk("hello world") is False
    assert has_cjk("hello 你好") is True
    assert has_cjk("") is False


@pytest.mark.asyncio
async def test_gemini_adapter_calls_api_and_returns_translation():
    adapter = GeminiFlashAdapter(api_key="fake-key")
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
    adapter = GeminiFlashAdapter(api_key="fake-key")
    fake_resp = AsyncMock()
    fake_resp.text = ""

    with patch.object(adapter, "_generate_async", return_value=fake_resp):
        with pytest.raises(RuntimeError, match="empty"):
            await adapter.translate("你好", source="zh", target="en")
