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
import math
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


def text(data: bytes, *, maths: bool = False, most: int = MAX_CHARS) -> Extracted:
    """The text of a PDF's pages, a line break between two pages. With `maths` (notes
    made into cards, D151), each page as `maths_text` reads it. Reading stops once `most`
    characters are found (D211: a study plan needs a file's first pages only)."""
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
            if size >= min(most, MAX_CHARS):
                break
    except PdfError:
        raise
    except Exception as exc:  # pypdf raises many kinds on a damaged file
        raise PdfError("unreadable") from exc
    joined = "\n".join(out)[: min(most, MAX_CHARS)]
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
# D166: and its big delimiters, at their four sizes ("\\left( … \\right)"), by code.
_CMEX.update(
    {chr(c): "(" for c in (0x00, 0x10, 0x12, 0x20)}
    | {chr(c): ")" for c in (0x01, 0x11, 0x13, 0x21)}
    | {chr(c): "[" for c in (0x02, 0x14, 0x22, 0x68)}
    | {chr(c): "]" for c in (0x03, 0x15, 0x23, 0x69)}
    | {chr(c): "{" for c in (0x08, 0x1A, 0x28, 0x6E)}
    | {chr(c): "}" for c in (0x09, 0x1B, 0x29, 0x6F)}
    | {chr(c): "⟨" for c in (0x0A, 0x1C, 0x2A, 0x44)}
    | {chr(c): "⟩" for c in (0x0B, 0x1D, 0x2B, 0x45)}
    | {"\x0c": "|", "\x0d": "‖", "p": "√", "q": "√", "r": "√", "s": "√"}
)
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


# --------------------------------------------------------------------------- #
# D166: the page read from where each piece of text is drawn
# --------------------------------------------------------------------------- #
# D151 joined pypdf's pieces, which run several drawing operations together when they share
# a font: a limit's "c" and the "f" opening a fraction's numerator came as one piece, and a
# fraction was flattened ("lim x→c f(x)–f(c) x–c" on the owner's card, 2026-11-20). D166
# reads each operation that draws text where it is drawn, its start, its end and its size,
# from pypdf's own layout reading (`recurse_to_target_op`, a module of pypdf's that is not
# its public interface: tested with pypdf 6.21.4, and anything that fails falls back to
# D151's reading). Then: a gap wider than a sixth of the size is a space, wherever the PDF
# came from; a thin line drawn with text just above and just below it, alone on their
# baselines, is a fraction, `\frac{...}{...}`; a smaller piece raised or lowered beside a
# sign is its script, or under "lim", "max", a sum, its limit.
_GAP = 0.15  # a gap wider than this share of the size is a space between words
_FRACTION_UP = 0.95  # a numerator's baseline at most this share of its size above the bar
_FRACTION_DOWN = 1.4  # a denominator's at most this share below it
_FRACTION_MOST = 0.45  # a bar at most this share of the page's width: wider is a table's rule
_LIMIT_BASES = frozenset({"lim", "sup", "inf", "max", "min", "limsup", "liminf", "∑", "∏", "⋃", "⋂"})


@dataclass
class _Run:
    text: str
    x: float  # where it starts
    x1: float  # where it ends
    y: float  # its baseline
    size: float
    font: str
    order: int  # its place in the order the page draws


def _runs(page: Any) -> list[_Run]:
    """Each text-drawing operation of a page, in the order drawn."""
    from pypdf._text_extraction._layout_mode._fixed_width_page import (
        recurse_to_target_op,
        set_font_operation,
    )
    from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager
    from pypdf.generic import ContentStream

    contents = page.get("/Contents")
    if contents is None:
        return []
    fonts = page._layout_mode_fonts()
    ops = iter(ContentStream(contents.get_object(), page.pdf, "bytes").operations)
    state = TextStateManager()
    found: list[Any] = []
    for operands, op in ops:
        if op in (b"BT", b"q"):
            _, shown = recurse_to_target_op(ops, state, b"ET" if op == b"BT" else b"Q", fonts, True)
            found.extend(shown)
        elif op == b"Tf":
            set_font_operation(state, fonts, operands)
        else:
            state.set_state_param(op, operands)
    runs = []
    for k, t in enumerate(found):
        if t.flip_vertical or t.rotated:
            raise ValueError("a page drawn upside down or turned: D151's reading")
        if not t.text or not t.text.strip():
            continue
        size = abs(float(t.font_size)) * math.hypot(float(t.transform[0]), float(t.transform[1]))
        runs.append(_Run(t.text, float(t.tx), float(t.displaced_tx), float(t.ty), size, str(t.font.name), k))
    return runs


def _bars(page: Any) -> list[tuple[float, float, float]]:
    """The thin horizontal lines a page draws (x from, x to, y): a fraction's bar, a rule."""
    from pypdf.generic import ContentStream

    contents = page.get("/Contents")
    if contents is None:
        return []
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    stack: list[list[float]] = []
    point: tuple[float, float] | None = None
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    boxes: list[tuple[tuple[float, float], tuple[float, float]]] = []
    bars = []

    def at(x: float, y: float) -> tuple[float, float]:
        return (x * ctm[0] + y * ctm[2] + ctm[4], x * ctm[1] + y * ctm[3] + ctm[5])

    for operands, op in ContentStream(contents.get_object(), page.pdf, "bytes").operations:
        if op == b"q":
            stack.append(ctm[:])
        elif op == b"Q":
            ctm = stack.pop() if stack else [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        elif op == b"cm" and len(operands) == 6:
            a, b, c, d, e, f = (float(v) for v in operands)
            m = ctm
            ctm = [a * m[0] + b * m[2], a * m[1] + b * m[3], c * m[0] + d * m[2], c * m[1] + d * m[3],
                   e * m[0] + f * m[2] + m[4], e * m[1] + f * m[3] + m[5]]  # fmt: skip
        elif op == b"m" and len(operands) == 2:
            point = at(float(operands[0]), float(operands[1]))
        elif op == b"l" and len(operands) == 2 and point is not None:
            end = at(float(operands[0]), float(operands[1]))
            lines.append((point, end))
            point = end
        elif op == b"re" and len(operands) == 4:
            x, y, w, h = (float(v) for v in operands)
            boxes.append((at(x, y), at(x + w, y + h)))
        elif op in (b"S", b"s"):
            for (x0, y0), (x1, y1) in lines:
                if abs(y1 - y0) < 0.5 and abs(x1 - x0) >= 3:
                    bars.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            lines, boxes, point = [], [], None
        elif op in (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"):
            for (x0, y0), (x1, y1) in boxes:
                if abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= 3:
                    bars.append((min(x0, x1), max(x0, x1), (y0 + y1) / 2))
            lines, boxes, point = [], [], None
        elif op == b"n":
            lines, boxes, point = [], [], None
    return bars


def _nearest_line(side: list[_Run], close: list[_Run], y: float) -> list[_Run]:
    """The runs on the baseline nearest the bar at `y` (among those `close` to it), with
    their scripts (from all on its `side`)."""
    if not close:
        return []
    near = min(close, key=lambda r: abs(r.y - y))
    return [r for r in side if abs(r.y - near.y) <= 0.75 * max(r.size, near.size)]


def _fractions(runs: list[_Run], bars: list[tuple[float, float, float]], width: float) -> list[_Run]:
    """The runs with each fraction made one run, `\\frac{numerator}{denominator}`, the
    narrowest bar first so that a fraction inside another is read first."""
    bars = [b for b in bars if b[1] - b[0] <= _FRACTION_MOST * width]  # wider: a table's rule
    if not bars:
        return runs
    # The page's text size: the size most of its characters are set in. A run smaller is a
    # script, never a line of text beside a fraction.
    by_size: dict[float, int] = {}
    for r in runs:
        by_size[round(r.size, 1)] = by_size.get(round(r.size, 1), 0) + len(r.text)
    body = max(by_size, key=lambda k: by_size[k])

    def over(r: _Run, x0: float, x1: float, y: float) -> bool:
        return (x0 - 1.5 <= r.x and r.x1 <= x1 + 1.5) and (
            0 < r.y - y <= _FRACTION_UP * r.size or 0 < y - r.y <= _FRACTION_DOWN * r.size
        )

    # A table's rules share their ends with the rules above and below them: no fraction.
    bars = [
        b
        for b in bars
        if not any(
            o is not b and abs(o[0] - b[0]) < 1 and abs(o[1] - b[1]) < 1 and abs(o[2] - b[2]) < 60
            for o in bars
        )
    ]
    for x0, x1, y in sorted(bars, key=lambda b: b[1] - b[0]):
        inside = [r for r in runs if x0 - 1.5 <= r.x and r.x1 <= x1 + 1.5]
        up = _nearest_line(
            [r for r in inside if r.y > y],
            [r for r in inside if 0 < r.y - y <= _FRACTION_UP * r.size],
            y,
        )
        down = _nearest_line(
            [r for r in inside if r.y < y],
            [r for r in inside if 0 < y - r.y <= _FRACTION_DOWN * r.size],
            y,
        )
        if not up or not down:
            continue
        # A fraction's lines are alone on their baselines: a line of text running on beside
        # them is text above or below a rule (an underline, an overline), not a fraction.
        reach = x1 - x0 + 40
        alone = not any(
            r not in group
            and r.size >= 0.9 * body
            and abs(r.y - group[0].y) < 0.1 * r.size
            and x0 - reach < r.x < x1 + reach
            and not any(over(r, *b) for b in bars)
            for group in (up, down)
            for r in runs
        )
        if not alone:
            continue
        size = max(r.size for r in up + down)
        frac = _Run(
            f"\\frac{{{_lay(up).strip()}}}{{{_lay(down).strip()}}}",
            min(r.x for r in up + down),
            max(r.x1 for r in up + down),
            y - 0.25 * size,  # the line's baseline: the bar sits on the maths' axis above it
            size,
            "",
            min(r.order for r in up + down),
        )
        taken = {id(r) for r in up + down}
        runs = sorted([r for r in runs if id(r) not in taken] + [frac], key=lambda r: r.order)
    return runs


def _lay(runs: list[_Run]) -> str:
    """Runs as text: a space where the page leaves one, a line break where a new line
    starts, scripts and limits marked, TeX's big signs named, a mark put on its sign."""
    out: list[str] = []
    base: _Run | None = None  # the line's last run of full size: what a script is measured against
    base_y = 0.0
    last: _Run | None = None
    last_y = 0.0
    line_y: float | None = None
    mark = ""
    kind_before = ""
    for k, r in enumerate(runs):
        cmex = "CMEX" in r.font.upper()
        text = "".join(_CMEX.get(c, c) for c in r.text) if cmex else r.text
        y = r.y
        if cmex:  # TeX's big signs hang from their top: they sit on the baseline of what follows
            after = next(
                (n for n in runs[k + 1 :] if "CMEX" not in n.font.upper() and n.size >= 0.9 * r.size), None
            )
            if after is not None and -2.5 * r.size < after.y - r.y <= 0.5 * r.size:
                y = after.y
            elif line_y is not None and abs(r.y - line_y) < 2 * r.size:
                y = line_y
        bare = text.strip()
        if bare and last is not None and all(c in _PRIMES for c in bare) and r.x - last.x1 < 0.6 * r.size:
            out.append(bare)  # a prime belongs to what it follows, however high it is set
            last, last_y = r, y
            continue
        if bare and all(unicodedata.combining(c) for c in bare):
            mark += bare  # TeX's slash for "not", drawn on its own over the next sign
            continue
        if mark:
            lead = len(text) - len(text.lstrip())
            if lead < len(text):
                text = text[:lead] + unicodedata.normalize("NFC", text[lead] + mark) + text[lead + 1 :]
            mark = ""
            bare = text.strip()
        gap = r.x - last.x1 if last is not None else 0.0
        if base is not None and line_y is not None:
            rise = y - base_y
            small = r.size <= base.size * _SCRIPT_SIZE
            beside = -0.3 * base.size <= r.x - base.x1 <= 0.6 * base.size
            limit = (base.text.strip() in _LIMIT_BASES or "CMEX" in base.font.upper()) and (
                base.x - base.size <= r.x <= base.x1 + 0.2 * base.size
            )
            going_on = (  # the same script, drawn in more than one piece
                kind_before != ""
                and last is not None
                and abs(r.size - last.size) < 0.1 * last.size
                and abs(y - last_y) < 0.2 * last.size
                and -0.3 * r.size <= gap < 0.6 * r.size
            )
            script = small and (
                going_on
                or (beside and base.size * _SCRIPT_RISE < abs(rise) < base.size * _SCRIPT_REACH)
                or (limit and base.size * 0.3 < abs(rise) < base.size * 1.9)
            )
            if script:
                kind = "^" if rise > 0 else "_"
                if all(c in _PRIMES for c in bare):
                    out.append(bare)
                elif going_on and kind == kind_before and out and out[-1].endswith("}"):
                    out[-1] = out[-1][:-1] + (" " if gap > _GAP * r.size else "") + bare + "}"
                else:
                    out.append(f"{kind}{{{bare}}}")
                kind_before = kind
                last, last_y = r, y
                continue
        kind_before = ""
        if line_y is not None and last is not None:
            if abs(y - line_y) > 0.6 * max(r.size, base.size if base else r.size):
                out.append("\n")
            elif gap > _GAP * max(r.size, last.size) and not out[-1].endswith((" ", "\n")):
                out.append(" ")
        out.append(text if out and not out[-1].endswith("\n") else text.lstrip())
        base, base_y, last, last_y, line_y = r, y, r, y, y
    return "".join(out)


def _placed_text(page: Any) -> str:
    """A page's text read from where each piece is drawn (D166)."""
    runs = _runs(page)
    if not runs:
        return ""
    width = float(page.mediabox.width) if page.mediabox is not None else 612.0
    return _lay(_fractions(runs, _bars(page), width))


def maths_text(page: Any) -> str:
    """A page's text with its words spaced and its mathematics marked (D151, D166); the
    page's plain text if anything goes wrong."""
    try:
        placed = _placed_text(page)
        if placed.strip():
            return re.sub(r"\n\s*\d{1,3}\s*$", "", placed.rstrip())
    except Exception:  # pypdf's layout reading changed, or an odd page: D151's reading
        pass
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
