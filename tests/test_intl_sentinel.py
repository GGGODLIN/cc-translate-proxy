"""Tests for the /intl per-workspace last-enable sentinel writer/reader."""
import json
import time
from pathlib import Path

import pytest

from cc_i18n_proxy.intl_sentinel import (
    SENTINEL_DIRNAME,
    read_last_enable,
    sentinel_path,
    write_last_enable,
)


def test_read_returns_none_when_no_sentinel(tmp_path: Path):
    assert read_last_enable(tmp_path, workspace_id="ws-a") is None


def test_write_then_read_roundtrip(tmp_path: Path):
    write_last_enable(tmp_path, workspace_id="ws-a", session_id="abc123")
    data = read_last_enable(tmp_path, workspace_id="ws-a")
    assert data is not None
    assert data["workspace_id"] == "ws-a"
    assert data["session_id"] == "abc123"
    assert isinstance(data["ts"], (int, float))
    assert data["ts"] > 0
    assert sentinel_path(tmp_path, workspace_id="ws-a").exists()


def test_per_workspace_isolation(tmp_path: Path):
    """Writes to one workspace MUST NOT touch another workspace's sentinel."""
    write_last_enable(tmp_path, workspace_id="ws-a", session_id="aaa")
    write_last_enable(tmp_path, workspace_id="ws-b", session_id="bbb")
    a = read_last_enable(tmp_path, workspace_id="ws-a")
    b = read_last_enable(tmp_path, workspace_id="ws-b")
    assert a["session_id"] == "aaa"
    assert b["session_id"] == "bbb"
    assert a["workspace_id"] == "ws-a"
    assert b["workspace_id"] == "ws-b"


def test_second_write_same_workspace_updates_ts_monotonically(tmp_path: Path):
    write_last_enable(tmp_path, workspace_id="ws-a", session_id="aaa")
    first = read_last_enable(tmp_path, workspace_id="ws-a")
    time.sleep(0.01)
    write_last_enable(tmp_path, workspace_id="ws-a", session_id="ccc")
    second = read_last_enable(tmp_path, workspace_id="ws-a")
    assert second["session_id"] == "ccc"
    assert second["ts"] > first["ts"]


def test_write_creates_parent_dirs(tmp_path: Path):
    nested = tmp_path / "missing" / "deep"
    write_last_enable(nested, workspace_id="ws", session_id="sid")
    assert sentinel_path(nested, workspace_id="ws").exists()
    assert (nested / SENTINEL_DIRNAME).is_dir()


def test_read_returns_none_on_broken_json(tmp_path: Path):
    target = sentinel_path(tmp_path, workspace_id="ws-a")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{ not json", encoding="utf-8")
    assert read_last_enable(tmp_path, workspace_id="ws-a") is None


def test_write_atomic_payload_keys(tmp_path: Path):
    write_last_enable(tmp_path, workspace_id="ws", session_id="sid")
    raw = sentinel_path(tmp_path, workspace_id="ws").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert set(parsed.keys()) == {"workspace_id", "session_id", "ts", "updated_at"}


def test_invalid_workspace_id_rejected(tmp_path: Path):
    """sentinel_path must reject path-traversal-ish workspace ids."""
    with pytest.raises(ValueError):
        sentinel_path(tmp_path, workspace_id="../escape")
    with pytest.raises(ValueError):
        sentinel_path(tmp_path, workspace_id="has/slash")
    # read with invalid id returns None (does not raise) so callers don't have to guard
    assert read_last_enable(tmp_path, workspace_id="../escape") is None
