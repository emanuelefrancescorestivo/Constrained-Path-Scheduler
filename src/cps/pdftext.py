"""
D90: the text of a PDF a student uploads to read a program (a course's syllabus, or a
whole semester's booklet).

pypdf (BSD-3-Clause, pure Python, in the `web` extra; the owner approved it on
2026-10-22) reads the text the PDF holds, page after page. It does no OCR: a scanned
page has no text, and the student is told so. The file is read and dropped: the
server keeps no copy (an upload above 1 MB passes through a temporary file the web
framework deletes when the request ends). What the student then saves is the topics,
the assessment, the credits and the hours read from it (`cps.syllabus`).

Limits keep an upload from costing the server much: a size, a number of pages and a
number of characters. A PDF crafted to make pypdf work for long within them is not
stopped: a limit D90 states.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

MAX_BYTES = 10 * 1024 * 1024  # 10 MB: a long program with pictures is far below
MAX_PAGES = 80  # pages read; a semester's booklet runs to 20 or 30
MAX_CHARS = 200_000  # characters kept: about 60 pages of text


class PdfError(ValueError):
    """Why a PDF gave no text: "large", "not_pdf", "locked", "unreadable", "empty",
    or "missing" when pypdf is not installed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Extracted:
    text: str
    pages: int  # the PDF's pages
    read: int  # the pages read, at most MAX_PAGES


def text(data: bytes) -> Extracted:
    """The text of a PDF's pages, a line break between two pages."""
    if len(data) > MAX_BYTES:
        raise PdfError("large")
    if b"%PDF-" not in data[:1024]:
        raise PdfError("not_pdf")
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # the `web` extra brings it
        raise PdfError("missing") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):  # a password the student must type
            raise PdfError("locked")
        pages = reader.pages
        count = len(pages)
        out: list[str] = []
        size = 0
        for page in list(pages)[:MAX_PAGES]:
            found = page.extract_text() or ""
            out.append(found)
            size += len(found)
            if size >= MAX_CHARS:
                break
    except PdfError:
        raise
    except Exception as exc:  # pypdf raises many kinds on a damaged file
        raise PdfError("unreadable") from exc
    joined = "\n".join(out)[:MAX_CHARS]
    if not joined.strip():
        raise PdfError("empty")
    return Extracted(joined, count, len(out))
