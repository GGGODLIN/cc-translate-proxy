"""Test file output emitter."""
from pathlib import Path

import pytest

from cc_i18n_proxy.emitter import FileEmitter


@pytest.mark.asyncio
async def test_emit_appends_text_to_file(tmp_path: Path):
    emitter = FileEmitter(tmp_path)
    await emitter.emit("session-x", "## Turn 1\n\n你好\n")
    await emitter.emit("session-x", "Hi back\n")
    await emitter.close()

    out = (tmp_path / "cc-i18n-session-x.md").read_text(encoding="utf-8")
    assert out == "## Turn 1\n\n你好\nHi back\n"


@pytest.mark.asyncio
async def test_emit_warning_event_renders_inline(tmp_path: Path):
    emitter = FileEmitter(tmp_path)
    await emitter.emit_warning("session-x", "翻譯失敗：原文 fallback")
    await emitter.close()

    out = (tmp_path / "cc-i18n-session-x.md").read_text(encoding="utf-8")
    assert "⚠" in out
    assert "翻譯失敗" in out


@pytest.mark.asyncio
async def test_emit_grace_degrades_on_oserror(tmp_path: Path, caplog, monkeypatch):
    emitter = FileEmitter(tmp_path)

    def boom(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(FileEmitter, "_append", staticmethod(boom))

    # Must not raise — grace degrade per spec §7.1 F7 extended pattern.
    await emitter.emit("session-x", "any text")
    assert "emit write failed" in caplog.text
