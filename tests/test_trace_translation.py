"""Tests for TRACE_TRANSLATION=1 stderr trace events."""
import io
import json
from contextlib import redirect_stderr

from cc_i18n_proxy.server import _trace


def test_trace_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRACE_TRANSLATION", raising=False)
    buf = io.StringIO()
    with redirect_stderr(buf):
        _trace({"event": "test"})
    assert buf.getvalue() == ""


def test_trace_enabled_emits_json(monkeypatch):
    monkeypatch.setenv("TRACE_TRANSLATION", "1")
    buf = io.StringIO()
    with redirect_stderr(buf):
        _trace({"event": "translation_mode_enter", "session_id": "abc"})
    out = buf.getvalue()
    assert out.startswith("[trace] ")
    payload = json.loads(out[len("[trace] "):].strip())
    assert payload == {"event": "translation_mode_enter", "session_id": "abc"}


def test_trace_unencodable_falls_back(monkeypatch):
    monkeypatch.setenv("TRACE_TRANSLATION", "1")
    buf = io.StringIO()

    class _Bad:
        def __repr__(self):
            raise TypeError("boom")

    with redirect_stderr(buf):
        _trace({"event": "x", "obj": _Bad()})
    assert "(unencodable event)" in buf.getvalue()
