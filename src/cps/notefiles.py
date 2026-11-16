"""
D134: the text of a file of notes a student uploads to make flashcards from it: a PDF,
a Word document (.docx), a PowerPoint deck (.pptx), an OpenDocument text or
presentation (.odt, .odp, what LibreOffice saves), Markdown or plain text.

Read on the server, for free: PDFs with pypdf (the `web` extra, D90), the others with
Python's own zip and XML readers. No new dependency, no model, nothing sent anywhere,
and the file is not kept: the text goes to the card rules (`cardmaker`), and the
student picks the cards. Rejected: OCR for photos and scans (a dependency for printed
text only; handwriting needs a model, which is the AI path, D12) and a free model
running on the server (gigabytes and seconds a page on the pilot's small server).

The shapes the rules read are kept where the file marks them: a heading becomes a
Markdown heading, an item of a list a "- " line, a word in bold `**…**`, a slide's
title a heading with its points as items under it. So a Word document's bold terms
become blanks to fill, and a slide's title and its points a list to recall. A whole
line in bold is a heading, as students write them, not a blank.

What is not read: photos and scans, a table's layout (its cells come as lines), the
order of text boxes placed by hand, formulas drawn as pictures or kept as equations.

Limits keep an upload from costing the server much: the file's size (10 MB, as a
PDF), the bytes unzipped (25 MB in all), the slides read, the characters kept. An XML
part that declares a DOCTYPE is refused: none of these formats needs one, and an
entity declared in it is how XML is made to swell (Python's expat also bounds it).
Old binary Office files (.doc, .ppt) are refused with how to save them anew.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from . import pdftext

MAX_BYTES = pdftext.MAX_BYTES  # 10 MB: a deck with pictures; the pictures are not read
MAX_UNZIPPED = 25 * 1024 * 1024  # all the XML parts read, unzipped
MAX_PARTS = 300  # slides of a deck read
MAX_CHARS = 60_000  # characters kept: the card rules' own limit (cardmaker.MAX_TEXT)
KINDS = ("pdf", "docx", "pptx", "odt", "odp", "text")
HEADING_WORDS = 10  # a whole line in bold up to this long is a heading
TITLE_WORDS = 12  # a slide's topmost box up to this long is its title, with none marked


class NotesError(ValueError):
    """Why a file gave no text: "large", "photo", "old_office", "unknown",
    "unreadable", "empty", or a PDF's own reason (`pdftext.PdfError`)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Extracted:
    text: str
    kind: str  # one of KINDS
    parts: int  # pages or slides in the file; 0 for a text
    read: int  # those read
    cut: bool  # the text was longer than MAX_CHARS


def text(name: str, data: bytes) -> Extracted:
    """The text of a file of notes, told by its first bytes, its name helping only
    for text files."""
    if len(data) > MAX_BYTES:
        raise NotesError("large")
    head = data[:16]
    if b"%PDF-" in data[:1024]:
        try:
            found = pdftext.text(data)
        except pdftext.PdfError as exc:
            raise NotesError(exc.reason) from exc
        return _done(found.text, "pdf", found.pages, found.read, indent=False)
    if head.startswith(b"PK\x03\x04"):
        return _zipped(data)
    if head.startswith(b"\xd0\xcf\x11\xe0"):  # an OLE2 file: Word 97 to 2003, PowerPoint the same
        raise NotesError("old_office")
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF")) or head[4:12] in (
        b"ftypheic",
        b"ftypheix",
        b"ftypmif1",
        b"ftypavif",
    ):
        raise NotesError("photo")
    if b"\x00" in data[:4096]:
        raise NotesError("unknown")
    try:
        plain = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        if not name.lower().endswith((".txt", ".md", ".markdown", ".text")):
            raise NotesError("unknown") from None
        plain = data.decode("cp1252", errors="replace")
    return _done(plain.replace("\r\n", "\n").replace("\r", "\n"), "text", 0, 0)


# A bullet a PDF draws with a symbol font comes out as a character of Unicode's
# private area (Word's round bullet is U+F0B7): read as an item.
_PDF_BULLET = re.compile(r"^\s*[\uf000-\uf0ff\u25aa\u25cf\u2023\u2043\u2219]\s*")


# D138: a PDF breaks lines where the page ran out of width, in the middle of a sentence.
# A line is joined to the one before when that one does not end a sentence (or a clause
# with a colon or semicolon) and this one starts in lower case, or the one before ends
# with a comma or a hyphen; an item, a heading or a numbered block starts its own line.
_NEW_LINE = re.compile(r"^\s*(?:[-*•▪◦●■➢►–—·+\uf000-\uf0ff]|\d{1,2}[.)]\s|#)")


def _joined(text: str) -> str:
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        last = out[-1].rstrip() if out else ""
        if (
            last
            and stripped
            and not _NEW_LINE.match(line)
            and not last.endswith((".", ":", ";", "?", "!"))
            and (stripped[0].islower() or last.endswith((",", "-")))
        ):
            out[-1] = (
                last[:-1] + stripped if last.endswith("-") and stripped[0].islower() else f"{last} {stripped}"
            )
        else:
            out.append(line)
    return "\n".join(out)


def _done(found: str, kind: str, parts: int, read: int, *, indent: bool = True) -> Extracted:
    """The text as the card rules read it: blanks inside a line made one, no more than
    one empty line in a row; a nested item's indent kept, except in a PDF, whose
    leading blanks are the layout's and whose symbol bullets become items."""
    lines = []
    for line in (_joined(found) if not indent else found).split("\n"):
        if not indent:
            line = _PDF_BULLET.sub("- ", line)
        lead = line[: len(line) - len(line.lstrip())].replace("\t", "  ")[:8] if indent else ""
        lines.append(lead + re.sub(r"[ \t]+", " ", line).strip() if line.strip() else "")
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    if not out:
        raise NotesError("empty")
    return Extracted(out[:MAX_CHARS], kind, parts, read, len(out) > MAX_CHARS)


# --------------------------------------------------------------------------- #
# Zipped XML: Office Open XML and OpenDocument


class _Parts:
    """The parts of a zip, each read once, all within MAX_UNZIPPED."""

    def __init__(self, data: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            self.names = set(self.zip.namelist())
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            raise NotesError("unreadable") from exc
        self.left = MAX_UNZIPPED

    def read(self, name: str) -> bytes:
        try:
            with self.zip.open(name) as part:  # read at most what is left, whatever the header says
                data = part.read(self.left + 1)
        except (KeyError, zipfile.BadZipFile, ValueError, OSError, RuntimeError) as exc:
            raise NotesError("unreadable") from exc
        if len(data) > self.left:
            raise NotesError("large")
        self.left -= len(data)
        return data

    def xml(self, name: str) -> ET.Element:
        data = self.read(name)
        if re.search(rb"<!DOCTYPE", data, re.IGNORECASE):
            raise NotesError("unreadable")
        try:
            return ET.fromstring(data)
        except ET.ParseError as exc:
            raise NotesError("unreadable") from exc


def _zipped(data: bytes) -> Extracted:
    parts = _Parts(data)
    if "word/document.xml" in parts.names:
        return _done(_docx(parts), "docx", 0, 0)
    if "ppt/presentation.xml" in parts.names:
        found, total, read = _pptx(parts)
        return _done(found, "pptx", total, read)
    if "content.xml" in parts.names and "mimetype" in parts.names:
        mime = parts.read("mimetype")[:100]
        if mime.endswith(b"opendocument.text"):
            return _done(_odf(parts.xml("content.xml"), parts, False)[0], "odt", 0, 0)
        if mime.endswith(b"opendocument.presentation"):
            found, total, read = _odf(parts.xml("content.xml"), parts, True)
            return _done(found, "odp", total, read)
    raise NotesError("unknown")


def _marked(segments: list[tuple[str, bool]]) -> str:
    """A paragraph's runs as one line, the bold ones between `**`, neighbours of the
    same weight joined first; a whole line in bold, short, is a heading."""
    merged: list[list] = []
    for words, bold in segments:
        if not words:
            continue
        if merged and (merged[-1][1] == bold or not words.strip()):
            merged[-1][0] += words
        else:
            merged.append([words, bold])
    plain = "".join(w for w, _b in merged).strip()
    if not plain:
        return ""
    if all(b for w, b in merged if w.strip()):  # a whole line in bold
        return f"## {plain}" if len(plain.split()) <= HEADING_WORDS else plain
    out = []
    for words, bold in merged:
        core = words.strip()
        if bold and core:
            lead = words[: len(words) - len(words.lstrip())]
            tail = words[len(words.rstrip()) :]
            out.append(f"{lead}**{core}**{tail}")
        else:
            out.append(words)
    return "".join(out).strip()


# Word ------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A heading's style, as Word names it in the languages it is sold in most here.
_HEADING_STYLE = re.compile(
    r"^(?:heading|titolo|titre|berschrift|.berschrift|ttulo|titulo|kop|nagwek)\s*(?P<n>\d)$|^(?:title|titolo|titre)$",
    re.IGNORECASE,
)


def _plain(line: str) -> str:
    """A line made a heading or an item: without the heading's marks or bold."""
    return line.removeprefix("## ").replace("**", "").strip()


def _on(element: ET.Element | None, attribute: str) -> bool:
    """A property switched on: present, and not set to false."""
    if element is None:
        return False
    return element.get(attribute, "true").lower() not in ("0", "false", "off", "none")


def _docx(parts: _Parts) -> str:
    root = parts.xml("word/document.xml")
    lines = []
    for p in root.iter(f"{_W}p"):
        props = p.find(f"{_W}pPr")
        style = props.find(f"{_W}pStyle") if props is not None else None
        heading = _HEADING_STYLE.match(
            re.sub(r"[^\w]", "", (style.get(f"{_W}val", "") if style is not None else ""))
        )
        listed = props is not None and (
            props.find(f"{_W}numPr") is not None
            or "list" in (style.get(f"{_W}val", "").lower() if style is not None else "")
        )
        segments = list(_word_runs(p))
        line = _marked(segments)
        if not line:
            continue
        if heading is not None:
            level = int(heading["n"]) if heading["n"] else 1
            lines.append("")
            lines.append(f"{'#' * min(level, 6)} {_plain(line)}")
        elif listed:
            depth = props.find(f"{_W}numPr/{_W}ilvl") if props is not None else None
            indent = "  " * min(int(depth.get(f"{_W}val", "0") if depth is not None else 0), 3)
            lines.append(f"{indent}- {_plain(line) if line.startswith('## ') else line}")
        else:
            if line.startswith("#"):
                lines.append("")
            lines.append(line)
    return "\n".join(lines)


def _word_runs(element: ET.Element) -> Iterator[tuple[str, bool]]:
    """A paragraph's runs in order, without those of a paragraph nested in it (a text
    box's, read as a paragraph of its own)."""
    for child in element:
        if child.tag == f"{_W}p":
            continue
        if child.tag == f"{_W}r":
            props = child.find(f"{_W}rPr")
            bold = props is not None and _on(props.find(f"{_W}b"), f"{_W}val")
            words = []
            for piece in child:
                if piece.tag == f"{_W}t":
                    words.append(piece.text or "")
                elif piece.tag == f"{_W}tab" or piece.tag in (f"{_W}br", f"{_W}cr"):
                    words.append(" ")
                elif piece.tag == f"{_W}noBreakHyphen":
                    words.append("-")
            yield "".join(words), bold
            # A text box inside the run holds paragraphs of its own, read on their own.
            continue
        yield from _word_runs(child)


# PowerPoint -------------------------------------------------------------------

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_SKIPPED_PLACES = ("sldNum", "ftr", "dt", "hdr")


def _slides(parts: _Parts) -> list[str]:
    """The slides' parts in the deck's order, from the presentation's list and its
    relationships; by their numbers when those are missing."""
    named = sorted(
        (n for n in parts.names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda n: int(re.sub(r"\D", "", n.rsplit("/", 1)[1])),
    )
    try:
        deck = parts.xml("ppt/presentation.xml")
        rels = parts.xml("ppt/_rels/presentation.xml.rels")
    except NotesError:
        return named
    targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(f"{_REL}Relationship")}
    order = []
    for slide in deck.iter(f"{_P}sldId"):
        target = targets.get(slide.get(f"{_R}id"), "")
        path = "ppt/" + target.lstrip("/").removeprefix("ppt/")
        if path in parts.names:
            order.append(path)
    return order or named


def _top(shape: ET.Element) -> int:
    offset = shape.find(f"{_P}spPr/{_A}xfrm/{_A}off")
    try:
        return int(offset.get("y", "0")) if offset is not None else 0
    except ValueError:
        return 0


def _short(lines: list[str]) -> bool:
    """One line of a title's length: what a text box placed at a slide's top holds."""
    return len(lines) == 1 and 0 < len(_plain(lines[0]).split()) <= TITLE_WORDS


def _pptx(parts: _Parts) -> tuple[str, int, int]:
    slides = _slides(parts)
    lines: list[str] = []
    for name in slides[:MAX_PARTS]:
        root = parts.xml(name)
        shapes = []
        for shape in root.iter(f"{_P}sp"):
            place = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
            kind = place.get("type", "body") if place is not None else "body"
            if kind not in _SKIPPED_PLACES:
                said = [line for p in shape.iter(f"{_A}p") if (line := _marked(list(_drawing_runs(p))))]
                shapes.append((kind, _top(shape), shape, said))
        # No title placeholder (a deck of text boxes placed by hand): the topmost short
        # box is the title, when there is something under it.
        if not any(k in ("title", "ctrTitle") for k, _t, _s, said in shapes if said):
            boxes = sorted((s for s in shapes if s[3]), key=lambda s: s[1])
            if len(boxes) > 1 and _short(boxes[0][3]):
                first = boxes[0]
                shapes = [("title", *first[1:]) if s is first else s for s in shapes]
        title: list[str] = []
        points: list[str] = []
        for kind, _top_at, shape, _said in shapes:
            for p in shape.iter(f"{_A}p"):
                line = _marked(list(_drawing_runs(p)))
                if not line:
                    continue
                if kind in ("title", "ctrTitle"):
                    title.append(_plain(line))
                else:
                    props = p.find(f"{_A}pPr")
                    depth = min(int(props.get("lvl", "0") or 0), 3) if props is not None else 0
                    points.append(f"{'  ' * depth}- {_plain(line) if line.startswith('## ') else line}")
        # A table's cells, one line each.
        for frame in root.iter(f"{_P}graphicFrame"):
            for p in frame.iter(f"{_A}p"):
                line = _marked(list(_drawing_runs(p)))
                if line:
                    points.append(_plain(line) if line.startswith("## ") else line)
        if title:
            lines += ["", f"# {' '.join(title)}"]
        elif points:
            lines.append("")
        lines += points
    return "\n".join(lines), len(slides), min(len(slides), MAX_PARTS)


def _drawing_runs(p: ET.Element) -> Iterator[tuple[str, bool]]:
    for piece in p:
        if piece.tag in (f"{_A}r", f"{_A}fld"):
            props = piece.find(f"{_A}rPr")
            bold = props is not None and props.get("b", "0") in ("1", "true")
            words = piece.find(f"{_A}t")
            yield (words.text or "") if words is not None else "", bold
        elif piece.tag == f"{_A}br":
            yield " ", False


# OpenDocument -----------------------------------------------------------------

_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_PRESENTATION = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
_SVG = "{urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0}"
_ODP_SKIPPED = ("page-number", "footer", "date-time", "header", "notes")


_ODF_HEADING = re.compile(r"^(?:Heading_20_(?P<n>\d)|Title|Heading)$")


def _odf_styles(*roots: ET.Element) -> tuple[set[str], dict[str, int]]:
    """The styles that set text in bold, and those of a heading with its level,
    through their parents: LibreOffice gives a heading changed by hand a style of its
    own whose parent is the heading's."""
    parent: dict[str, str] = {}
    bold: set[str] = set()
    level: dict[str, int] = {}
    for root in roots:
        for style in root.iter(f"{_STYLE}style"):
            name = style.get(f"{_STYLE}name", "")
            parent[name] = style.get(f"{_STYLE}parent-style-name", "")
            props = style.find(f"{_STYLE}text-properties")
            if props is not None and props.get(f"{_FO}font-weight") in ("bold", "600", "700", "800", "900"):
                bold.add(name)
            outline = style.get(f"{_STYLE}default-outline-level")
            found = _ODF_HEADING.match(name)
            if outline and outline.isdigit():
                level[name] = int(outline)
            elif found is not None:
                level[name] = int(found["n"] or 1)
    for name in list(parent):
        seen, current = 0, name
        while current and seen < 10:  # a chain of parents, bounded
            named = _ODF_HEADING.match(current)  # a parent kept in another part, by its name
            if name not in level and current in level:
                level[name] = level[current]
            elif name not in level and named is not None:
                level[name] = int(named["n"] or 1)
            if current in bold:
                bold.add(name)
            current, seen = parent.get(current, ""), seen + 1
    return bold, level


def _length(value: str) -> float:
    """A length as ODF writes it ("1.5cm", "0.4in"), in centimetres; 0 when unread."""
    found = re.fullmatch(r"(-?[\d.]+)(cm|mm|in|pt|pc|px)?", value.strip())
    if found is None:
        return 0.0
    scale = {"cm": 1, "mm": 0.1, "in": 2.54, "pt": 2.54 / 72, "pc": 2.54 / 6, "px": 2.54 / 96}
    try:
        return float(found[1]) * scale.get(found[2] or "cm", 1)
    except ValueError:
        return 0.0


def _odf(root: ET.Element, parts: _Parts, deck: bool) -> tuple[str, int, int]:
    roots = [root]
    if "styles.xml" in parts.names:
        roots.append(parts.xml("styles.xml"))
    bold, heading_of = _odf_styles(*roots)
    lines: list[str] = []

    def runs(element: ET.Element, strong: bool) -> Iterator[tuple[str, bool]]:
        if element.text:
            yield element.text, strong
        for child in element:
            if child.tag == f"{_TEXT}span":
                yield from runs(child, strong or child.get(f"{_TEXT}style-name", "") in bold)
            elif child.tag == f"{_TEXT}s":
                yield " " * int(child.get(f"{_TEXT}c", "1") or 1), strong
            elif child.tag in (f"{_TEXT}tab", f"{_TEXT}line-break"):
                yield " ", strong
            elif child.tag not in (f"{_TEXT}note", f"{_TEXT}annotation"):
                yield from runs(child, strong)
            if child.tail:
                yield child.tail, strong

    def block(element: ET.Element, depth: int) -> None:
        """Headings, paragraphs and lists in order; in a deck, a frame's paragraphs
        are its points."""
        for child in element:
            if child.tag == f"{_TEXT}h":
                line = _marked(list(runs(child, False)))
                if line:
                    level = int(child.get(f"{_TEXT}outline-level", "1") or 1)
                    lines.extend(["", f"{'#' * min(level, 6)} {_plain(line)}"])
            elif child.tag == f"{_TEXT}p":
                style = child.get(f"{_TEXT}style-name", "")
                line = _marked(list(runs(child, style in bold)))
                if not line:
                    continue
                if style in heading_of and not deck:
                    lines.extend(["", f"{'#' * min(heading_of[style], 6)} {_plain(line)}"])
                elif depth or deck:
                    indent = "  " * min(max(depth - 1, 0), 3)
                    lines.append(f"{indent}- {_plain(line) if line.startswith('## ') else line}")
                else:
                    lines.extend(["", line] if line.startswith("#") else [line])
            elif child.tag == f"{_TEXT}list":
                block(child, depth + 1)
            elif child.tag not in (f"{_TEXT}note", f"{_TEXT}annotation", f"{_TEXT}tracked-changes"):
                block(child, depth)

    def said(frame: ET.Element) -> list[str]:
        return [line for p in frame.iter(f"{_TEXT}p") if (line := _marked(list(runs(p, False))))]

    def frames_of(element: ET.Element) -> Iterator[ET.Element]:
        """A slide's frames, in groups too, without its speaker notes."""
        for child in element:
            if child.tag == f"{_DRAW}frame":
                if child.get(f"{_PRESENTATION}class") not in _ODP_SKIPPED:
                    yield child
            elif child.tag != f"{_PRESENTATION}notes":
                yield from frames_of(child)

    if deck:
        pages = list(root.iter(f"{_DRAW}page"))
        for page in pages[:MAX_PARTS]:
            frames = list(frames_of(page))
            titles = [f for f in frames if f.get(f"{_PRESENTATION}class") == "title"]
            if not any(said(f) for f in titles):  # placed by hand: the topmost short box
                boxes = sorted((f for f in frames if said(f)), key=lambda f: _length(f.get(f"{_SVG}y", "0")))
                titles = [boxes[0]] if len(boxes) > 1 and _short(said(boxes[0])) else []
            heading = " ".join(_plain(line) for f in titles for line in said(f))
            lines.extend(["", f"# {heading}"] if heading else [""])
            for frame in frames:
                if frame not in titles:
                    block(frame, 0)
        return "\n".join(lines), len(pages), min(len(pages), MAX_PARTS)
    body = root.find("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body")
    block(body if body is not None else root, 0)
    return "\n".join(lines), 0, 0
