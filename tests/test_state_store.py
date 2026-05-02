"""Tests for state.json atomic R/W + mtime cache + invalid recovery."""
import json
import time

import pytest

from cc_i18n_proxy.providers.state import (
    StateStore,
    write_active_head,
)


@pytest.fixture
def state_path(tmp_path):
    return tmp_path / "state.json"


def test_read_returns_none_when_missing(state_path):
    store = StateStore(state_path)
    assert store.read_active_head() is None


def test_write_then_read_roundtrip(state_path):
    write_active_head(state_path, "groq-llama", updated_by="test")
    store = StateStore(state_path)
    assert store.read_active_head() == "groq-llama"


def test_write_is_atomic(state_path, tmp_path):
    """No tmp file should remain after a successful write."""
    write_active_head(state_path, "groq-llama", updated_by="test")
    leftover = list(tmp_path.glob("state.json.tmp*"))
    assert leftover == []


def test_mtime_cache_avoids_reparsing(state_path):
    write_active_head(state_path, "x", updated_by="test")
    store = StateStore(state_path)
    first = store.read_active_head()
    # Stub the read path to detect re-reads
    parse_count = {"n": 0}
    orig_parse = store._parse_file
    def _spy(*a, **kw):
        parse_count["n"] += 1
        return orig_parse(*a, **kw)
    store._parse_file = _spy
    second = store.read_active_head()
    third = store.read_active_head()
    assert first == second == third
    # Without mtime change, should not re-parse beyond initial cache miss
    assert parse_count["n"] == 0


def test_mtime_change_triggers_reparse(state_path):
    write_active_head(state_path, "x", updated_by="test")
    store = StateStore(state_path)
    assert store.read_active_head() == "x"
    # Bump mtime by writing again with a different head
    time.sleep(0.01)
    write_active_head(state_path, "y", updated_by="test")
    assert store.read_active_head() == "y"


def test_broken_json_is_renamed_and_falls_back(state_path):
    state_path.write_text("not valid json {{{")
    store = StateStore(state_path)
    head = store.read_active_head()
    assert head is None
    # Original file renamed
    broken = list(state_path.parent.glob("state.json.broken-*"))
    assert len(broken) == 1


def test_write_includes_metadata(state_path):
    write_active_head(state_path, "groq-llama", updated_by="user_via_render_ui")
    payload = json.loads(state_path.read_text())
    assert payload["active_head"] == "groq-llama"
    assert payload["updated_by"] == "user_via_render_ui"
    assert "updated_at" in payload  # ISO-8601 timestamp


def test_read_full_state_returns_dict(state_path):
    write_active_head(state_path, "x", updated_by="daemon_init")
    store = StateStore(state_path)
    state = store.read_full_state()
    assert state["active_head"] == "x"
    assert state["updated_by"] == "daemon_init"
