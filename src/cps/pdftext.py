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
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

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


def text(data: bytes, *, maths: bool = False) -> Extracted:
    """The text of a PDF's pages, a line break between two pages. With `maths` (notes
    made into cards, D151), each page as `maths_text` reads it."""
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
            found = maths_text(page) if maths else page.extract_text() or ""
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
                text = maths_text(page)
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


# --------------------------------------------------------------------------- #
# D151: mathematics in a page of notes, for flashcards
# --------------------------------------------------------------------------- #

# pypdf's text runs words into the mathematics beside them in a LaTeX PDF ("a functionf",
# "of classC1") and flattens what is raised or lowered ("Rn" for R to the n, "(f|U)−1" for
# an inverse). Its layout reading spaces words right but keeps the flattening, and it reads
# two columns across. So a page is built here from the pieces pypdf reads, in the order the
# PDF draws them: a piece joins the one before with a space where the layout reading has
# one between those words; a piece set smaller and raised or lowered is a superscript
# (`^{...}`) or a subscript (`_{...}`), which a card draws as such; a mark drawn on its own
# over the next sign (TeX's slash for "not") is put on it ("=" and the slash: "≠"); TeX's
# big operators, in a font with no Unicode names, are named again. Anything that fails is
# the page's plain text, as before.
_SCRIPT_SIZE = 0.82  # a piece this much smaller than the line's text, or less, may be a script
_SCRIPT_RISE = 0.12  # ... when raised or lowered by more than this share of the line's size
_SCRIPT_REACH = 0.7  # ... and less than this: farther is another line (a footnote)
# TeX's extension font (cmex) draws the big operators with letters' codes, and carries no
# Unicode names: text and display sizes of the sum, the product and the integral.
_CMEX = {"P": "∑", "X": "∑", "Q": "∏", "Y": "∏", "R": "∫", "Z": "∫"}
_PRIMES = frozenset("′″‴'")


@dataclass(frozen=True)
class _Piece:
    text: str
    x: float
    y: float
    size: float
    font: str


def _pieces(page: Any) -> list[_Piece]:
    found: list[_Piece] = []

    def visit(text: str, cm: Any, tm: Any, font: Any, size: float) -> None:
        if not text:
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        scale = abs(size * (tm[0] or tm[3] or 1) * (cm[0] or cm[3] or 1))
        name = str(font.get("/BaseFont", "")) if hasattr(font, "get") else ""
        found.append(_Piece(text, float(x), float(y), float(scale), name))

    page.extract_text(visitor_text=visit)
    return found


def _words(before: str, after: str) -> tuple[str, str] | None:
    a = re.search(r"\S+$", before)
    b = re.match(r"\S+", after)
    return (a.group(0), b.group(0)) if a and b else None


def _spaced(layout: str, before: str, after: str) -> bool:
    """Whether the layout reading separates the word `before` ends with from the one
    `after` starts with, and never writes them together."""
    pair = _words(before, after)
    if pair is None:
        return False
    return f"{pair[0]} {pair[1]}" in layout and f"{pair[0]}{pair[1]}" not in layout


def _glued(layout: str, before: str, after: str) -> bool:
    """Whether the layout reading writes them together, and never apart."""
    pair = _words(before, after)
    if pair is None:
        return False
    return f"{pair[0]}{pair[1]}" in layout and f"{pair[0]} {pair[1]}" not in layout


def maths_text(page: Any) -> str:
    """A page's text with its words spaced and its mathematics marked (D151); the page's
    plain text if anything goes wrong."""
    try:
        pieces = _pieces(page)
        layout = " ".join(page.extract_text(extraction_mode="layout").split())
        out: list[str] = []  # what is kept, with its marks
        shadow: list[str] = []  # the same without the marks, to compare with the layout reading
        base: _Piece | None = None  # the line's last piece of text: what a script is measured against
        mark, mark_space = "", ""  # a combining mark waiting for the sign it is drawn over
        last_kind = ""  # "^" or "_" while the pieces before were a script of that kind
        for p in pieces:
            cmex = "CMEX" in p.font.upper()
            words = "".join(_CMEX.get(c, c) for c in p.text) if cmex else p.text
            bare = words.strip()
            if bare and all(unicodedata.combining(c) for c in bare):
                mark += bare
                mark_space = mark_space or words[: len(words) - len(words.lstrip())]
                continue
            if mark:
                lead = len(words) - len(words.lstrip())
                if lead < len(words):
                    words = mark_space + words[lead:]
                    i = len(mark_space)
                    words = words[:i] + unicodedata.normalize("NFC", words[i] + mark) + words[i + 1 :]
                mark, mark_space = "", ""
            tail = "".join(shadow[-12:])
            if base is not None and bare and "\n" not in words:
                rise = p.y - base.y
                small = p.size <= base.size * _SCRIPT_SIZE
                near = base.size * _SCRIPT_RISE < abs(rise) < base.size * _SCRIPT_REACH
                if small and near:
                    kind = "^" if rise > 0 else "_"
                    if all(c in _PRIMES for c in bare):
                        out.append(bare)
                    elif last_kind == kind and out and out[-1].endswith("}"):
                        out[-1] = out[-1][:-1] + bare + "}"  # the same script, drawn in two pieces
                    else:
                        if tail and not tail[-1].isspace() and _spaced(layout, tail, bare):
                            out.append(" ")
                            shadow.append(" ")
                        out.append(f"{kind}{{{bare}}}")
                    shadow.append(bare)
                    last_kind = kind
                    continue
            last_kind = ""
            if tail and words:
                if not tail[-1].isspace() and not words[0].isspace() and _spaced(layout, tail, words):
                    out.append(" ")
                    shadow.append(" ")
                elif words[0].isspace() and "\n" not in words[:1] and _glued(layout, tail, words.lstrip()):
                    words = words.lstrip()  # pypdf's own space where the page has none: "f (c)"
            out.append(words)
            shadow.append(words)
            if bare and not cmex:
                base = p
        # A page's number alone on its last line is not part of the notes.
        found = re.sub(r"\n\s*\d{1,3}\s*$", "", "".join(out).rstrip())
        return found if found.strip() else (page.extract_text() or "")
    except Exception:  # an odd page: its plain text, as before D151
        return page.extract_text() or ""
