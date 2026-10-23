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


# --------------------------------------------------------------------------- #
# D95: a PDF of a student's notes, kept with the post that shares it
# --------------------------------------------------------------------------- #

NOTE_PAGES = 300  # a semester's notes in one file can run long; the size bounds it too


@dataclass(frozen=True)
class Cleaned:
    data: bytes  # the PDF as kept: its pages, without what names its author or runs
    pages: int
    text: str  # what can be read of it, for flashcards (D84); "" for a scan


# What a PDF may carry that is not its pages: who made it and with what (the document
# information and the XMP metadata, often the student's real name), and what runs when
# it opens (JavaScript, actions). Photos lose their location the same way (D18).
_CATALOG_DROPPED = ("/Metadata", "/OpenAction", "/AA", "/AcroForm", "/Names", "/PieceInfo")
_PAGE_DROPPED = ("/AA", "/Metadata", "/PieceInfo")


def clean(data: bytes) -> Cleaned:
    """A PDF of notes to keep: its pages written again, without its metadata and
    without anything that runs when it is opened; and its text, as far as it has one
    (a scan has none, which is fine for notes)."""
    if len(data) > MAX_BYTES:
        raise PdfError("large")
    if not data.startswith(b"%PDF-"):
        raise PdfError("not_pdf")
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import NameObject
    except ImportError as exc:  # the `web` extra brings it
        raise PdfError("missing") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfError("locked")
        count = len(reader.pages)
        if count == 0:
            raise PdfError("empty")
        if count > NOTE_PAGES:
            raise PdfError("large")
        writer = PdfWriter()
        found: list[str] = []
        size = 0
        for page in reader.pages:
            if size < MAX_CHARS:
                text = page.extract_text() or ""
                found.append(text)
                size += len(text)
            kept = writer.add_page(page)
            for key in _PAGE_DROPPED:
                kept.pop(NameObject(key), None)
        for key in _CATALOG_DROPPED:
            writer._root_object.pop(NameObject(key), None)
        writer.metadata = None
        out = io.BytesIO()
        writer.write(out)
    except PdfError:
        raise
    except Exception as exc:  # pypdf raises many kinds on a damaged file
        raise PdfError("unreadable") from exc
    return Cleaned(out.getvalue(), count, "\n".join(found)[:MAX_CHARS])
