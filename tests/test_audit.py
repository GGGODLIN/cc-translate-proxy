"""Test JSONL audit log writer."""
import json
from pathlib import Path

import pytest

from cc_i18n_proxy.audit import AuditLogWriter, TurnEntry


@pytest.mark.asyncio
async def test_audit_writes_jsonl_entry(tmp_path: Path):
    writer = AuditLogWriter(tmp_path)
    entry = TurnEntry(
        timestamp="2026-05-01T01:23:45+00:00",
        session_id="abc",
        turn_id=42,
        user_zh="你好",
        user_en="Hello",
        assistant_en="Hi",
        assistant_zh="嗨",
        translation_sources={"user": "cache", "assistant": "translator_api"},
        tokens={"input_anthropic": 10, "output_anthropic": 5, "translator_api_calls": 1},
        translation_status={"user": "ok", "assistant": "ok"},
    )
    await writer.write(entry)
    await writer.close()

    log_file = tmp_path / "abc.jsonl"
    assert log_file.exists()
    line = log_file.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["session_id"] == "abc"
    assert parsed["turn_id"] == 42
    assert parsed["user_en"] == "Hello"
    assert parsed["translation_sources"]["user"] == "cache"


@pytest.mark.asyncio
async def test_audit_appends_multiple_entries(tmp_path: Path):
    writer = AuditLogWriter(tmp_path)
    for i in range(3):
        await writer.write(TurnEntry(
            timestamp="t",
            session_id="s",
            turn_id=i,
            user_zh="u", user_en="u",
            assistant_en="a", assistant_zh="a",
            translation_sources={"user": "cache", "assistant": "translator_api"},
            tokens={"input_anthropic": 0, "output_anthropic": 0, "translator_api_calls": 0},
            translation_status={"user": "ok", "assistant": "ok"},
        ))
    await writer.close()

    lines = (tmp_path / "s.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["turn_id"] for line in lines] == [0, 1, 2]


@pytest.mark.asyncio
async def test_audit_rejects_unsafe_session_id(tmp_path: Path):
    writer = AuditLogWriter(tmp_path)
    bad_entry = TurnEntry(
        timestamp="2026-05-01T00:00:00+00:00",
        session_id="../../etc/passwd",
        turn_id=0,
        user_zh="x", user_en="x",
        assistant_en="y", assistant_zh="y",
        translation_sources={"user": "cache", "assistant": "translator_api"},
        tokens={"input_anthropic": 0, "output_anthropic": 0, "translator_api_calls": 0},
        translation_status={"user": "ok"},
    )
    with pytest.raises(ValueError, match="unsafe session_id"):
        await writer.write(bad_entry)


@pytest.mark.asyncio
async def test_audit_grace_degrades_on_oserror(tmp_path: Path, caplog, monkeypatch):
    writer = AuditLogWriter(tmp_path)
    entry = TurnEntry(
        timestamp="2026-05-01T00:00:00+00:00",
        session_id="sess-abc",
        turn_id=0,
        user_zh="x", user_en="x",
        assistant_en="y", assistant_zh="y",
        translation_sources={"user": "cache", "assistant": "translator_api"},
        tokens={"input_anthropic": 0, "output_anthropic": 0, "translator_api_calls": 0},
        translation_status={"user": "ok"},
    )

    def boom(path, line):
        raise OSError("disk full")
    monkeypatch.setattr(AuditLogWriter, "_append", staticmethod(boom))

    # Must not raise — spec §7.1 F7 grace degrade.
    await writer.write(entry)
    assert "audit write failed" in caplog.text

def test_turn_entry_retry_of_default_is_none():
    entry = TurnEntry(
        timestamp="2026-05-02T00:00:00Z",
        session_id="testabc",
        turn_id=1,
        user_zh="hi", user_en="hi",
        assistant_en="hi", assistant_zh="hi",
        translation_sources={"user": "cache", "assistant": "translator_api"},
        tokens={"input_anthropic": 0},
    )
    assert entry.retry_of is None
    assert entry.workspace_id == ""
    assert entry.workspace_name == ""


def test_turn_entry_retry_of_serializes():
    from dataclasses import asdict
    entry = TurnEntry(
        timestamp="2026-05-02T00:00:00Z",
        session_id="testabc",
        turn_id=2,
        user_zh="hi", user_en="hi",
        assistant_en="hi", assistant_zh="hi-retried",
        translation_sources={"user": "cache", "assistant": "translator_api"},
        tokens={"input_anthropic": 0},
        retry_of=1,
        workspace_id="W-X",
        workspace_name="cc-i18n-proxy",
    )
    d = asdict(entry)
    assert d["retry_of"] == 1
    assert d["workspace_id"] == "W-X"
    assert d["workspace_name"] == "cc-i18n-proxy"

