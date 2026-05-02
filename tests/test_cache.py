"""Test SQLite-backed translation cache."""
from pathlib import Path

import pytest

from cc_i18n_proxy.cache import TranslationCache, content_hash


def test_content_hash_stable():
    h1 = content_hash("你好")
    h2 = content_hash("你好")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_content_hash_differs_for_different_text():
    assert content_hash("你好") != content_hash("再見")


@pytest.mark.asyncio
async def test_cache_get_set_roundtrip(tmp_path: Path):
    cache = await TranslationCache.create(tmp_path / "cache.db")
    h = content_hash("你好")
    assert await cache.get(h) is None

    await cache.set(h, "Hello", source="zh", target="en")
    assert await cache.get(h) == "Hello"

    await cache.close()


@pytest.mark.asyncio
async def test_cache_persists_across_instances(tmp_path: Path):
    db_path = tmp_path / "cache.db"
    c1 = await TranslationCache.create(db_path)
    h = content_hash("你好")
    await c1.set(h, "Hello", source="zh", target="en")
    await c1.close()

    c2 = await TranslationCache.create(db_path)
    assert await c2.get(h) == "Hello"
    await c2.close()
