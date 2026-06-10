"""File-based output emitter (Tier a). Tier c will add WebSocket alongside."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class FileEmitter:
    def __init__(self, emit_dir: Path):
        self._dir = emit_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_")
        return self._dir / f"cc-i18n-{safe}.md"

    async def emit(self, session_id: str, text: str) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._append, self._path(session_id), text)
            except OSError as exc:
                # Spec §7.1 F7 (extended to render): emit failure must not block main flow.
                log.error("emit write failed for %s: %s", session_id, exc)

    async def emit_warning(self, session_id: str, message: str) -> None:
        formatted = f"\n> ⚠ {message}\n\n"
        await self.emit(session_id, formatted)

    @staticmethod
    def _append(path: Path, text: str) -> None:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(text)

    async def close(self) -> None:
        return  # nothing to flush — append uses fresh file handle each call
