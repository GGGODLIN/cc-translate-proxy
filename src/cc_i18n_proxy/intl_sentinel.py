"""Per-workspace sentinel files for the most recent /intl enable event.

The proxy writes one sentinel file per workspace every time a
`[CC_I18N_PROXY:ENABLE_THIS_SESSION:...]` marker fires for that workspace.
The render server reads them from a polling endpoint so the detail page can
detect "a fresh /intl just happened in MY workspace, redirect me to it".

Per-workspace storage gives us automatic cross-workspace isolation: an /intl
event in workspace `ws_b` cannot redirect a tab pinned to a session in `ws_a`,
because that tab polls a different sentinel file.

Atomic write pattern: write to `<name>.tmp`, then rename. Concurrent readers
see either the old file or the new file, never a partially-written one.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SENTINEL_DIRNAME = "last-enable"

_SAFE_WS_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def sentinel_path(home: Path, *, workspace_id: str) -> Path:
    """Return the sentinel path for a workspace, validating the workspace id."""
    if not _SAFE_WS_RE.match(workspace_id):
        raise ValueError(f"invalid workspace_id: {workspace_id!r}")
    return home / SENTINEL_DIRNAME / f"{workspace_id}.json"


def write_last_enable(home: Path, *, workspace_id: str, session_id: str) -> None:
    target = sentinel_path(home, workspace_id=workspace_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "workspace_id": workspace_id,
        "session_id": session_id,
        "ts": time.time(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_last_enable(home: Path, *, workspace_id: str) -> dict[str, Any] | None:
    try:
        target = sentinel_path(home, workspace_id=workspace_id)
    except ValueError:
        return None
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("last-enable sentinel unreadable for ws=%s: %s", workspace_id, exc)
        return None
