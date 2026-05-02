"""Tests for OpenAICompatAdapter."""
import httpx
import pytest
import respx

from cc_i18n_proxy.translator import (
    AuthError,
    ClientError,
    NetworkError,
    OpenAICompatAdapter,
    RateLimitError,
    ServerError,
)


@pytest.fixture
def adapter():
    return OpenAICompatAdapter(
        base_url="https://api.example.com/openai/v1",
        api_key="test-key",
        model="test-model",
        timeout=5.0,
    )


@pytest.mark.asyncio
async def test_translate_success(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Hello world"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        )
        result = await adapter.translate("你好世界", source="zh", target="en")
    assert result.text == "Hello world"
    assert result.source_lang == "zh"
    assert result.target_lang == "en"


@pytest.mark.asyncio
async def test_translate_strips_trailing_whitespace(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "  Hi  \n"}}]})
        )
        result = await adapter.translate("嗨", source="zh", target="en")
    assert result.text == "Hi"


@pytest.mark.asyncio
async def test_translate_strips_thinking_block(adapter):
    """Reasoning models (qwen3, deepseek-r1) prepend <think>…</think>; strip it."""
    raw = "<think>\nOkay let me translate this carefully…\n</think>\n\n會話 UUID：abc 翻譯模式啟動。"
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
        )
        result = await adapter.translate("Session UUID: abc", source="en", target="zh")
    assert result.text == "會話 UUID：abc 翻譯模式啟動。"
    assert "<think>" not in result.text
    assert "translate this carefully" not in result.text


@pytest.mark.asyncio
async def test_translate_strips_only_trailing_thinking_block(adapter):
    """Multiple thinking blocks (rare but possible) all get stripped."""
    raw = "<think>first thought</think><think>second thought</think>\n結果"
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
        )
        result = await adapter.translate("X", source="en", target="zh")
    assert result.text == "結果"


@pytest.mark.asyncio
async def test_translate_unclosed_think_left_alone(adapter):
    """If <think> is unclosed (malformed reasoning), don't swallow the whole answer."""
    raw = "<think>orphan thought without close\n會話 UUID：abc"
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
        )
        result = await adapter.translate("X", source="en", target="zh")
    assert "會話 UUID：abc" in result.text


@pytest.mark.asyncio
async def test_translate_429_raises_rate_limit(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(429, headers={"retry-after": "30"}, text="rate_limited")
        )
        with pytest.raises(RateLimitError) as exc_info:
            await adapter.translate("X", source="zh", target="en")
    assert "30" in str(exc_info.value)  # retry-after surfaced


@pytest.mark.asyncio
async def test_translate_500_raises_server_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(500, text="boom"))
        with pytest.raises(ServerError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_503_raises_server_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(503, text="unavail"))
        with pytest.raises(ServerError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_401_raises_auth_error(adapter):
    # Body intentionally echoes a fake bearer token to verify the message strips it
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(401, text="invalid Authorization: Bearer leaked-token")
        )
        with pytest.raises(AuthError) as exc_info:
            await adapter.translate("X", source="zh", target="en")
    assert "leaked-token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_translate_400_raises_client_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(400, text="bad request"))
        with pytest.raises(ClientError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_404_raises_client_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(404, text="model not found"))
        with pytest.raises(ClientError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_timeout_raises_network_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(side_effect=httpx.TimeoutException("timed out"))
        with pytest.raises(NetworkError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_connection_error_raises_network_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(NetworkError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_request_error_raises_network_error(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(side_effect=httpx.ReadError("read failed"))
        with pytest.raises(NetworkError):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_empty_response_raises_runtime(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
        )
        with pytest.raises(RuntimeError, match="empty"):
            await adapter.translate("X", source="zh", target="en")


@pytest.mark.asyncio
async def test_translate_sends_authorization_header(adapter):
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )
        await adapter.translate("X", source="zh", target="en")
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_translate_sends_correct_payload(adapter):
    import json
    with respx.mock(base_url="https://api.example.com/openai/v1") as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )
        await adapter.translate("你好", source="zh", target="en")
    payload = json.loads(route.calls.last.request.read())
    assert payload["model"] == "test-model"
    # Tier (g): rules go in system role; user content carries the input.
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert "anti-tofu-spangenese" in payload["messages"][0]["content"].lower() \
        or "translate" in payload["messages"][0]["content"].lower()
    assert payload["messages"][1]["role"] == "user"
    assert "你好" in payload["messages"][1]["content"]
    # SiliconFlow strict-validates temperature as JSON number (float), not int
    assert payload["temperature"] == 0.0
    assert isinstance(payload["temperature"], float)


@pytest.mark.asyncio
async def test_no_auth_header_when_api_key_empty():
    """Local Ollama doesn't need a key."""
    adapter = OpenAICompatAdapter(
        base_url="http://localhost:11434/v1", api_key="", model="qwen3:7b", timeout=30.0,
    )
    with respx.mock(base_url="http://localhost:11434/v1") as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        )
        await adapter.translate("X", source="zh", target="en")
    sent = route.calls.last.request
    assert "authorization" not in sent.headers
