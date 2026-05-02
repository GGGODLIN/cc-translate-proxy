"""SQLite-backed translation cache. Persistent across proxy restarts."""
from __future__ import annotations

import hashlib
from pathlib import Path

import aiosqlite


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS translations (
    content_hash  TEXT PRIMARY KEY,
    translation   TEXT NOT NULL,
    source_lang   TEXT NOT NULL,
    target_lang   TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class TranslationCache:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    @classmethod
    async def create(cls, path: Path) -> "TranslationCache":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        await conn.executescript(_SCHEMA)
        await conn.commit()
        return cls(conn)

    async def get(self, content_hash: str) -> str | None:
        cur = await self._conn.execute(
            "SELECT translation FROM translations WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cur.fetchone()
        await cur.close()
        return row[0] if row else None

    async def set(self, content_hash: str, translation: str, *, source: str, target: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO translations (content_hash, translation, source_lang, target_lang) VALUES (?, ?, ?, ?)",
            (content_hash, translation, source, target),
        )
        await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()
