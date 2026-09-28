"""Console output hardening.

Windows decodes piped and redirected output with the legacy code page (cp1252 on
most Western installs), which cannot encode characters like pi or the prime sign
that the demo prints. An interactive console is fine; `python demo.py > out.txt`
is a crash. The tests simulate the cp1252 stream directly, so they bite on Linux.
"""

from __future__ import annotations

import io
import sys

import pytest

from cps.console import ensure_utf8_output


def test_a_cp1252_stream_cannot_print_the_characters_the_demo_uses(monkeypatch):
    """The premise, so the next test is known to be meaningful."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    with pytest.raises(UnicodeEncodeError):
        print("mean \u03c0* and V\u2032")


def test_ensure_utf8_output_lets_a_cp1252_stream_print_them(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_utf8_output()
    print("\u03c0*")
    assert raw.getvalue() == "\u03c0*\n".encode("utf-8")


def test_unencodable_text_is_replaced_never_raised(monkeypatch):
    """A crash in the middle of a plan is worse than one odd character."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_utf8_output()
    print("Analyse \u2192 plan")
    assert b"plan" in raw.getvalue()


def test_a_stream_without_reconfigure_is_left_alone(monkeypatch):
    class Bare:
        def write(self, _):  # pragma: no cover - only needs to exist
            return 0

    monkeypatch.setattr(sys, "stdout", Bare())
    monkeypatch.setattr(sys, "stderr", Bare())
    ensure_utf8_output()  # must not raise
