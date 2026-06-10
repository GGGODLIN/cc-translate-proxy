"""Parse providers.toml, load .env, build TranslatorChain factory."""
from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from cc_i18n_proxy.translator import (
    GeminiNativeAdapter,
    NamedAdapter,
    OpenAICompatAdapter,
    TranslatorChain,
)

log = logging.getLogger(__name__)

_VALID_KINDS: frozenset[str] = frozenset({"openai-compat", "gemini-native"})
_REQUIRED_OPENAI_COMPAT = ("model", "base_url", "api_key_env", "display_name")
_REQUIRED_GEMINI_NATIVE = ("api_key_env", "model", "display_name")


@dataclass(frozen=True)
class ProviderEntry:
    name: str
    kind: str
    api_key_env: str
    model: str
    display_name: str
    base_url: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class ProvidersConfig:
    default_chain: list[str]
    providers: dict[str, ProviderEntry] = field(default_factory=dict)


def load_providers_config(
    toml_path: Path,
    *,
    dotenv_path: Path | None = None,
) -> ProvidersConfig:
    """Parse providers.toml and (optionally) populate os.environ from .env.

    Existing os.environ keys are NOT overridden by .env values.
    Raises FileNotFoundError if toml_path missing; ValueError on parse/validation errors.
    """
    if dotenv_path is not None and dotenv_path.exists():
        load_dotenv(dotenv_path=str(dotenv_path), override=False)

    if not toml_path.exists():
        raise FileNotFoundError(f"providers.toml not found at {toml_path}")

    try:
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"parse error in {toml_path}: {exc}") from exc

    default_chain = raw.get("default_chain", [])
    if not isinstance(default_chain, list) or not all(isinstance(x, str) for x in default_chain):
        raise ValueError("default_chain must be a list of provider names")
    if not default_chain:
        raise ValueError("default_chain must not be empty")

    providers_raw = raw.get("providers", {})
    if not isinstance(providers_raw, dict):
        raise ValueError("[providers] must be a table")

    providers: dict[str, ProviderEntry] = {}
    for name, entry in providers_raw.items():
        kind = entry.get("kind")
        if kind not in _VALID_KINDS:
            raise ValueError(
                f"provider {name!r}: invalid kind {kind!r}; must be one of {_VALID_KINDS}"
            )
        required = _REQUIRED_OPENAI_COMPAT if kind == "openai-compat" else _REQUIRED_GEMINI_NATIVE
        for field_name in required:
            if field_name not in entry:
                raise ValueError(f"provider {name!r}: missing required field {field_name!r}")

        providers[name] = ProviderEntry(
            name=name,
            kind=kind,
            api_key_env=entry["api_key_env"],
            model=entry["model"],
            display_name=entry["display_name"],
            base_url=entry.get("base_url", ""),
            enabled=entry.get("enabled", True),
        )

    return ProvidersConfig(default_chain=default_chain, providers=providers)


def build_chain_from_config(
    cfg: ProvidersConfig,
    *,
    active_head_reader: Callable[[], str | None],
) -> TranslatorChain:
    """Build TranslatorChain from parsed config + a head reader callable.

    Filters out providers with missing api_key_env (logged warning).
    Raises ValueError if the resulting default_chain is empty.
    """
    enabled_by_name: dict[str, NamedAdapter] = {}
    for name, entry in cfg.providers.items():
        if not entry.enabled:
            continue
        adapter = _build_adapter(entry)
        if adapter is None:
            continue
        enabled_by_name[name] = NamedAdapter(name=name, adapter=adapter)

    default_chain: list[NamedAdapter] = []
    for name in cfg.default_chain:
        if name in enabled_by_name:
            default_chain.append(enabled_by_name[name])
        else:
            log.warning("default_chain entry %r is not in enabled providers; skipping", name)

    if not default_chain:
        raise ValueError(
            "default_chain is empty after filtering disabled / missing-key providers"
        )

    return TranslatorChain(
        default_chain=default_chain,
        enabled_by_name=enabled_by_name,
        active_head_reader=active_head_reader,
    )


def _build_adapter(
    entry: ProviderEntry,
) -> OpenAICompatAdapter | GeminiNativeAdapter | None:
    if entry.api_key_env:
        api_key = os.environ.get(entry.api_key_env, "")
        if not api_key:
            log.warning(
                "provider %r: api_key_env=%r not set in environment; disabling",
                entry.name, entry.api_key_env,
            )
            return None
    else:
        api_key = ""

    try:
        if entry.kind == "openai-compat":
            timeout = 30.0 if _is_local(entry.base_url) else 10.0
            return OpenAICompatAdapter(
                base_url=entry.base_url, api_key=api_key, model=entry.model, timeout=timeout,
            )
        if entry.kind == "gemini-native":
            return GeminiNativeAdapter(api_key=api_key, model=entry.model)
    except ValueError as exc:
        log.warning("provider %r: adapter rejected config: %s; disabling", entry.name, exc)
        return None
    raise ValueError(f"unreachable kind: {entry.kind}")


def _is_local(base_url: str) -> bool:
    lowered = base_url.lower()
    return any(needle in lowered for needle in ("localhost", "127.0.0.1", "[::1]"))
