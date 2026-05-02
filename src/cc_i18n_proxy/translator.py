"""Translator adapter abstraction. Tier (a): GeminiNativeAdapter."""
from __future__ import annotations

import asyncio
import json as _json
import logging as _logging
import re
from dataclasses import dataclass, field
from datetime import datetime as _datetime, timezone as _timezone
from typing import Any, Callable, Protocol

import google.generativeai as genai
import httpx as _httpx

_log = _logging.getLogger(__name__)


# Explicit escapes — plan literal `豈` rendered as U+8C48 (regular CJK), not
# U+F900 (CJK Compat start), which silently included Hangul (U+AC00-D7AF).
_CJK_RE = re.compile(
    r"[一-鿿"   # CJK Unified Ideographs (BMP)
    r"㐀-䶿"   # CJK Extension A
    r"豈-﫿]"  # CJK Compatibility Ideographs
)


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


@dataclass(frozen=True)
class TranslationResult:
    text: str
    source_lang: str
    target_lang: str


# === Error class hierarchy ===

class RateLimitError(Exception):
    """HTTP 429 — retryable; chain advances to next adapter."""


class ServerError(Exception):
    """HTTP 5xx — retryable."""


class NetworkError(Exception):
    """Timeout, DNS failure, ConnectionError — retryable."""


class AuthError(Exception):
    """HTTP 401 — fatal by conservative design (spec §4.4.1).

    A 401 from one adapter does not necessarily mean other adapters' keys
    are also bad, but trying them blindly would burn requests against
    every provider in the chain. Surfaced as TranslatorConfigError so
    the user sees the actual problem in audit + UI.
    """


class ClientError(Exception):
    """HTTP 400 / 404 / 422 / other 4xx — fatal; request shape error."""


class TranslatorChainExhausted(Exception):
    """All retryable adapters in the chain failed; pipeline maps to translate_api_outage."""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__(f"all chain adapters exhausted; {len(errors)} retryable errors")


class TranslatorConfigError(Exception):
    """Fatal error from an adapter (auth/client); chain stops short of trying next.

    Pipeline maps to translator_config_error.
    """

    def __init__(self, *, provider: str, cause: Exception,
                 failover_attempts: list[str] | None = None,
                 failover_errors: list[dict[str, Any]] | None = None):
        self.provider = provider
        self.cause = cause
        self.failover_attempts = list(failover_attempts) if failover_attempts is not None else []
        self.failover_errors = list(failover_errors) if failover_errors is not None else []
        super().__init__(f"config error from {provider}: {cause}")


# === Annotated result wrapper ===

@dataclass(frozen=True)
class AnnotatedResult:
    """Wraps a TranslationResult with provenance for audit logging."""

    result: TranslationResult
    provider_name: str
    failover_attempts: list[str] = field(default_factory=list)
    failover_errors: list[dict[str, Any]] = field(default_factory=list)


class Translator(Protocol):
    async def translate(self, text: str, *, source: str, target: str) -> TranslationResult: ...


_LANG_NAMES = {
    "zh": "Traditional Chinese",
    "zh-Hant": "Traditional Chinese",
    "zh-Hans": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
}

_SYSTEM_RULES = """You translate developer/LLM content for a Taiwan developer reader who reads English fluently.

Style ("anti-tofu-spangenese"):
- KEEP English when the term is a technical proper noun with no natural Chinese rendering: API, hook, commit, token, prompt, system prompt, session, cron, RAG, JWT, embedding, framework / library / tool / protocol names (FastAPI, React, gRPC, etc.).
- TRANSLATE when the word is a verb, adjective, or abstract concept with a natural Chinese rendering (e.g. "spark joy" → 觸動, "core" → 核心, "explore" → 探索).
- Decision rule: ask "could this English word translate naturally? would translating make it more readable?". If yes, translate.
- Cap: at most 2-3 English jargon per sentence; 3+ "translatable but kept" English words = over-stuffed, translate more.

Output rules:
- Preserve code blocks, inline code (`...`), URLs, and markdown formatting verbatim.
- Output ONLY the translation. No preamble, no explanation, no quotes, no trailing labels like "Translation:".
- If the input is already in the target language, return it unchanged."""

_USER_TEMPLATE = """Translate from {source_name} to {target_name}.

Input:
{text}

Output:"""


def _concat_for_legacy_role(system: str, user: str) -> str:
    """Fallback for adapters whose API can't take a system role separately.

    Concatenates with double newline so the LLM still distinguishes rules
    from payload. Use this when the provider SDK has no system_instruction /
    system role / equivalent. Behaviour matches the pre-Tier(g) single-prompt
    mode — correctness preserved, but loses prompt-prefix caching benefit.
    """
    return f"{system}\n\n{user}"


# === Adapter contract for prompt construction ===
#
# When implementing a new translator adapter:
# 1. Prefer the provider's native system mechanism if available:
#    - OpenAI-compat: messages[0].role = "system"
#    - Anthropic: top-level `system` parameter
#    - google-generativeai: GenerativeModel(system_instruction=...)
# 2. If unsupported, fall back to _concat_for_legacy_role(_SYSTEM_RULES, user_prompt).
#    Output is identical to the pre-split single-prompt mode — defeats prefix
#    caching but correctness is unaffected.
# 3. Keep _SYSTEM_RULES stable across calls. Caching only kicks in when the
#    system prefix doesn't change per request.


class GeminiNativeAdapter:
    """Translator adapter backed by google-generativeai SDK.

    Supports Gemini and Gemma model families served through ai.google.dev.
    Tries native system_instruction first; falls back to single-prompt
    concat for models that don't accept system_instruction (older Gemma).
    """

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        if not api_key:
            raise ValueError("GEMINI_API_KEY required for GeminiNativeAdapter")
        genai.configure(api_key=api_key)
        self._model_name = model
        try:
            self._model = genai.GenerativeModel(
                model, system_instruction=_SYSTEM_RULES,
            )
            self._uses_system_instruction = True
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "GenerativeModel(%s) rejected system_instruction (%s); "
                "falling back to concatenated prompt",
                model, exc,
            )
            self._model = genai.GenerativeModel(model)
            self._uses_system_instruction = False

    async def translate(self, text: str, *, source: str, target: str) -> TranslationResult:
        user_prompt = _USER_TEMPLATE.format(
            text=text,
            source_name=_LANG_NAMES.get(source, source),
            target_name=_LANG_NAMES.get(target, target),
        )
        prompt = (
            user_prompt if self._uses_system_instruction
            else _concat_for_legacy_role(_SYSTEM_RULES, user_prompt)
        )
        resp = await self._generate_async(prompt)
        translated = (resp.text or "").strip()
        if not translated:
            raise RuntimeError("translator returned empty response")
        return TranslationResult(text=translated, source_lang=source, target_lang=target)

    async def _generate_async(self, prompt: str):
        return await asyncio.to_thread(self._model.generate_content, prompt)


# Backward-compat alias — kept until all imports migrated. Removed in later task.
GeminiFlashAdapter = GeminiNativeAdapter


class OpenAICompatAdapter:
    """Translator adapter for any OpenAI-compatible /v1/chat/completions endpoint.

    Tested against Groq, DeepSeek, Ollama, OpenRouter, SiliconFlow. base_url should
    include the version segment (e.g. "https://api.groq.com/openai/v1").
    """

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def translate(self, text: str, *, source: str, target: str) -> TranslationResult:
        user_prompt = _USER_TEMPLATE.format(
            text=text,
            source_name=_LANG_NAMES.get(source, source),
            target_name=_LANG_NAMES.get(target, target),
        )
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_RULES},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
        }
        try:
            async with _httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
                resp = await client.post("/chat/completions", json=payload, headers=headers)
        except _httpx.TimeoutException as exc:
            raise NetworkError(f"timeout: {exc}") from exc
        except _httpx.ConnectError as exc:
            raise NetworkError(f"connect: {exc}") from exc
        except _httpx.RequestError as exc:
            raise NetworkError(f"request: {exc}") from exc

        # Map HTTP status → error class
        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after", "")
            raise RateLimitError(f"rate limited (retry-after={retry_after})")
        if 500 <= resp.status_code < 600:
            raise ServerError(f"upstream {resp.status_code}: {resp.text[:200]}")
        if resp.status_code == 401:
            raise AuthError("unauthorized (401)")
        if 400 <= resp.status_code < 500:
            raise ClientError(f"{resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
            translated = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, _json.JSONDecodeError) as exc:
            raise RuntimeError(f"malformed response: {exc}; body={resp.text[:200]}") from exc

        # Strip reasoning-model thinking blocks (qwen3, deepseek-r1, etc.).
        # The translator's internal monologue is noise to downstream audit + render UI.
        translated = re.sub(r"<think>.*?</think>\s*", "", translated, flags=re.DOTALL).strip()

        if not translated:
            raise RuntimeError("translator returned empty response")

        return TranslationResult(text=translated, source_lang=source, target_lang=target)


@dataclass(frozen=True)
class NamedAdapter:
    """A Translator adapter paired with a stable name used in chain references and audit."""

    name: str
    adapter: Translator


def _error_to_dict(provider_name: str, exc: Exception) -> dict:
    """Serialize an adapter error for audit/UI display."""
    payload = {
        "provider": provider_name,
        "code": _classify_code(exc),
        "message": str(exc)[:500],
        "timestamp": _datetime.now(_timezone.utc).isoformat(),
    }
    msg = str(exc)
    if "retry-after=" in msg:
        retry_value = msg.split("retry-after=")[-1].removesuffix(")").strip()
        if retry_value:
            payload["retry_after"] = retry_value
    return payload


def _classify_code(exc: Exception) -> int | str:
    if isinstance(exc, RateLimitError):
        return 429
    if isinstance(exc, AuthError):
        return 401
    if isinstance(exc, ClientError):
        msg = str(exc)
        if msg[:3].isdigit():
            return int(msg[:3])
        return "4xx"
    if isinstance(exc, ServerError):
        msg = str(exc)
        if "upstream " in msg:
            try:
                return int(msg.split("upstream ")[1].split(":")[0])
            except (IndexError, ValueError):
                return "5xx"
        return "5xx"
    if isinstance(exc, NetworkError):
        return "network"
    return "unknown"


class TranslatorChain:
    """Chain of NamedAdapter, with reactive failover on retryable errors and
    fatal short-circuit on auth/client errors. Active head comes from a callable
    so state.json polling can be wired separately."""

    def __init__(self, *,
                 default_chain: list[NamedAdapter],
                 enabled_by_name: dict[str, NamedAdapter],
                 active_head_reader: Callable[[], str | None]):
        if not default_chain:
            raise ValueError("default_chain must not be empty")
        self._default_chain = default_chain
        self._enabled_by_name = enabled_by_name
        self._read_head = active_head_reader

    async def translate(self, text: str, *, source: str, target: str) -> AnnotatedResult:
        head_name = self._read_head()
        ordered = self._chain_starting_from(head_name)
        return await self._run_ordered(text, source=source, target=target, ordered=ordered)

    async def translate_with_head(
        self, text: str, *, source: str, target: str, head_name: str,
    ) -> AnnotatedResult:
        """Like translate(), but bypasses active_head_reader and uses the given head.

        Retry semantics: caller explicitly chooses which provider to attempt first.
        Falls through default_chain (excluding head_name) on retryable errors, just
        like the normal translate() flow but with a hand-picked head.
        """
        if head_name not in self._enabled_by_name:
            raise ValueError(f"head_name {head_name!r} not in enabled providers")
        head = self._enabled_by_name[head_name]
        rest = [a for a in self._default_chain if a.name != head_name]
        ordered = [head] + rest
        return await self._run_ordered(text, source=source, target=target, ordered=ordered)

    async def _run_ordered(
        self, text: str, *, source: str, target: str, ordered: list[NamedAdapter],
    ) -> AnnotatedResult:
        """Shared core: try each adapter in order with retryable / fatal split."""
        attempts: list[str] = []
        errors: list[dict[str, Any]] = []
        for named in ordered:
            try:
                result = await named.adapter.translate(text, source=source, target=target)
                return AnnotatedResult(
                    result=result, provider_name=named.name,
                    failover_attempts=list(attempts), failover_errors=list(errors),
                )
            except (RateLimitError, ServerError, NetworkError) as exc:
                attempts.append(named.name)
                errors.append(_error_to_dict(named.name, exc))
                continue
            except (AuthError, ClientError) as exc:
                _log.error("translator_config_error from %s: %s", named.name, exc)
                raise TranslatorConfigError(
                    provider=named.name, cause=exc,
                    failover_attempts=list(attempts),
                    failover_errors=list(errors) + [_error_to_dict(named.name, exc)],
                ) from exc
        raise TranslatorChainExhausted(errors)

    def _chain_starting_from(self, head_name: str | None) -> list[NamedAdapter]:
        """Build adapter list to attempt. See spec §2 'Chain ordering algorithm'."""
        if head_name is None or head_name not in self._enabled_by_name:
            if head_name is not None:
                _log.warning("active_head %r not in enabled providers; using default_chain", head_name)
            return list(self._default_chain)
        head = self._enabled_by_name[head_name]
        rest = [a for a in self._default_chain if a.name != head_name]
        return [head] + rest
