"""Append-only JSONL audit log per session."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnEntry:
    timestamp: str
    session_id: str
    turn_id: int
    user_zh: str
    user_en: str
    assistant_en: str
    assistant_zh: str
    translation_sources: dict[str, str]
    tokens: dict[str, int]

    translation_status: dict[str, str] = field(default_factory=dict)
    translation_providers: dict[str, str] = field(default_factory=dict)
    failover_attempts: dict[str, list[str]] = field(default_factory=dict)
    failover_errors: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    retry_of: int | None = None
    workspace_id: str = ""
    workspace_name: str = ""
    prompt_source: str = ""


# Defense in depth: session_id ends up in a filename. Restrict to safe chars
# even though pipeline derives it from a sha256 hex. Caller-side bug → loud failure.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class AuditLogWriter:
    def __init__(self, audit_dir: Path):
        self._dir = audit_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def write(self, entry: TurnEntry) -> None:
        if not _SAFE_SESSION_ID.match(entry.session_id):
            raise ValueError(f"unsafe session_id for audit log: {entry.session_id!r}")
        path = self._dir / f"{entry.session_id}.jsonl"
        line = json.dumps(asdict(entry), ensure_ascii=False) + "\n"
        async with self._lock:
            try:
                await asyncio.to_thread(self._append, path, line)
            except OSError as exc:
                # Spec §7.1 F7: audit failure must not block main flow.
                log.error("audit write failed for %s: %s", entry.session_id, exc)

    @staticmethod
    def _append(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(line)

    async def close(self) -> None:
        return  # nothing to flush — append uses fresh file handle each call
