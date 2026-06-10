"""State.json atomic R/W + mtime cache for active_head selection."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def write_active_head(state_path: Path, head_name: str, *, updated_by: str) -> None:
    """Atomic write: tmp file + rename. Concurrent readers see old or new, never partial."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_head": head_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by,
    }
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        tmp_path.replace(state_path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


class StateStore:
    """Cached reader of state.json. Re-parses only when mtime changes.

    Not thread-safe in the strict sense — concurrent readers may double-parse
    on a stale-mtime detection (cheap, idempotent) and the broken-file rename
    swallows FileNotFoundError to handle two readers racing to rename the same
    corrupt file.
    """

    def __init__(self, state_path: Path):
        self._path = state_path
        self._cached_state: dict[str, Any] | None = None
        self._cached_mtime: float = -1

    def invalidate(self) -> None:
        self._cached_state = None
        self._cached_mtime = -1

    def read_active_head(self) -> str | None:
        state = self.read_full_state()
        if state is None:
            return None
        return state.get("active_head")

    def read_full_state(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return None
        if mtime == self._cached_mtime:
            return self._cached_state
        parsed = self._parse_file()
        if parsed is None:
            return None
        self._cached_state = parsed
        self._cached_mtime = mtime
        return parsed

    def _parse_file(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("state.json broken: %s; renaming aside", exc)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            broken = self._path.with_suffix(self._path.suffix + f".broken-{ts}")
            try:
                self._path.rename(broken)
            except FileNotFoundError:
                pass
            return None
        except OSError as exc:
            log.warning("state.json read failed: %s", exc)
            return None
