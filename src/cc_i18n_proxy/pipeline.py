"""In-process translation pipeline. Coordinates schema parser + cache + translator chain."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from cc_i18n_proxy.cache import TranslationCache, content_hash
from cc_i18n_proxy.schema_parser import (
    BlockKind,
    classify_user_content,
    rebuild_user_content,
)
from cc_i18n_proxy.translator import (
    AnnotatedResult,
    Translator,
    TranslatorChainExhausted,
    TranslatorConfigError,
    _error_to_dict,
    has_cjk,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    """Per-request translation outcome.

    UI consumers MUST read `user_status` (and `assistant_status` once Task 13
    surfaces it) for health signals. `sources["user"]` reports the dominant
    source-of-translation (cache / translator_api / fallback_passthrough) and
    is silent for no-CJK input — it is not a status field.
    """

    body: dict[str, Any]
    sources: dict[str, str]
    user_provider: str = ""
    user_failover_attempts: list[str] = field(default_factory=list)
    user_failover_errors: list[dict[str, Any]] = field(default_factory=list)
    user_status: str = "ok"


class TranslationPipeline:
    def __init__(self, *, translator: Translator, cache: TranslationCache):
        self._translator = translator
        self._cache = cache

    async def translate_request_body(self, body: dict[str, Any]) -> PipelineResult:
        new_body = dict(body)
        messages = list(body.get("messages", []))
        new_messages: list[dict[str, Any]] = []
        sources_seen: set[str] = set()
        user_provider = ""
        user_failover_attempts: list[str] = []
        user_failover_errors: list[dict] = []
        user_status = "ok"
        had_translation_attempt = False
        had_outage = False
        had_config_error = False

        for msg in messages:
            if msg.get("role") != "user":
                new_messages.append(msg)
                continue

            content = msg.get("content", "")
            classified = classify_user_content(content)
            translated_map: dict[int | None, str] = {}

            for kind, text, idx in classified:
                if kind is not BlockKind.TRANSLATE or text is None:
                    continue
                if not has_cjk(text):
                    continue

                had_translation_attempt = True
                h = content_hash(text)
                cached = await self._cache.get(h)
                if cached is not None:
                    translated_map[idx] = cached
                    sources_seen.add("cache")
                    continue

                try:
                    annotated_or_result = await self._translator.translate(
                        text, source="zh", target="en",
                    )
                    if isinstance(annotated_or_result, AnnotatedResult):
                        translated_text = annotated_or_result.result.text
                        if not user_provider:
                            user_provider = annotated_or_result.provider_name
                        for n in annotated_or_result.failover_attempts:
                            if n not in user_failover_attempts:
                                user_failover_attempts.append(n)
                        user_failover_errors.extend(annotated_or_result.failover_errors)
                    else:
                        translated_text = annotated_or_result.text
                    translated_map[idx] = translated_text
                    await self._cache.set(h, translated_text, source="zh", target="en")
                    sources_seen.add("translator_api")
                except TranslatorChainExhausted as exc:
                    had_outage = True
                    user_failover_errors.extend(exc.errors)
                    log.warning("chain exhausted for user text; passthrough")
                    sources_seen.add("fallback_passthrough")
                except TranslatorConfigError as exc:
                    had_config_error = True
                    user_failover_attempts.extend(exc.failover_attempts)
                    user_failover_errors.extend(exc.failover_errors)
                    log.error("translator_config_error for user text from %s; passthrough", exc.provider)
                    sources_seen.add("fallback_passthrough")
                except Exception as exc:
                    had_outage = True
                    user_failover_errors.append(_error_to_dict("unknown", exc))
                    log.exception("unexpected translator failure; passthrough")
                    sources_seen.add("fallback_passthrough")

            new_msg = dict(msg)
            new_msg["content"] = rebuild_user_content(content, translated_map)
            new_messages.append(new_msg)

        new_body["messages"] = new_messages

        if had_config_error:
            user_status = "translator_config_error"
        elif had_outage:
            user_status = "translate_api_outage"
        elif had_translation_attempt and "fallback_passthrough" in sources_seen \
             and "translator_api" not in sources_seen and "cache" not in sources_seen:
            user_status = "fallback_passthrough"

        sources = {"user": _dominant(sources_seen)}
        return PipelineResult(
            body=new_body, sources=sources,
            user_provider=user_provider,
            user_failover_attempts=user_failover_attempts,
            user_failover_errors=user_failover_errors,
            user_status=user_status,
        )


def _dominant(seen: set[str]) -> str:
    if "translator_api" in seen:
        return "translator_api"
    if "fallback_passthrough" in seen:
        return "fallback_passthrough"
    if "cache" in seen:
        return "cache"
    return "fallback_passthrough"
