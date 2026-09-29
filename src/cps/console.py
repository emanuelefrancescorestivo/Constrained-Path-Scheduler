"""Console output hardening.

On Windows, a program run from an interactive console can print any Unicode, but
the same program with its output piped or redirected (`python demo.py > out.txt`)
encodes with the legacy code page, cp1252 on most Western installs. That code
page has no pi and no prime sign, both of which the demo prints, so the run dies
with `UnicodeEncodeError` half-way through. It works on Linux and macOS, where
the default is UTF-8, which is exactly why it goes unnoticed until someone on
Windows runs it.

The fix is to state the encoding instead of inheriting it, and to replace what
cannot be encoded instead of raising: a crash in the middle of a study plan is
worse than one odd character.
"""

from __future__ import annotations

import sys


def ensure_utf8_output() -> None:
    """Make stdout and stderr encode as UTF-8, replacing anything unencodable.

    Streams without `reconfigure` (a plain file-like object, some test doubles)
    are left alone, and a stream that refuses reconfiguration is skipped rather
    than allowed to abort start-up.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue
