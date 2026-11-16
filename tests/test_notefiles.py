"""
D134: flashcards from a file of notes, read on the server for free. What must hold:
a Word document, a PowerPoint deck, an OpenDocument text or deck, a PDF and a text
file each give their text with the shapes the card rules read (headings, items, words
in bold, a slide's title over its points), a deck in its own order and without its
slide numbers; what is not read (a photo, an old Office file, a file of another kind,
one too large, a part that swells unzipped or declares a DOCTYPE) is refused with
why; the Flashcards page sends a file in one step, and the file is not kept.

The files here are written by the test, part by part, as the formats lay them out;
`notefiles` was also tried on files LibreOffice wrote (DECISIONS.md D134).
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime
from html import unescape
from pathlib import Path

import pytest
from test_program import pdf

from cps import cardmaker, notefiles, service

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
PML = (
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
ODF = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"'
)


def _zip(parts: dict[str, str | bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return out.getvalue()


def _para(text: str, *, style: str = "", listed: bool = False, bold: tuple[str, ...] = ()) -> str:
    props = ""
    if style or listed:
        props = "<w:pPr>"
        props += f'<w:pStyle w:val="{style}"/>' if style else ""
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if listed else ""
        props += "</w:pPr>"
    runs = ""
    for piece in [text, *bold] if not bold else _pieces(text, bold):
        strong = piece in bold
        runs += (
            f'<w:r>{"<w:rPr><w:b/></w:rPr>" if strong else ""}<w:t xml:space="preserve">{piece}</w:t></w:r>'
        )
    return f"<w:p>{props}{runs}</w:p>"


def _pieces(text: str, bold: tuple[str, ...]) -> list[str]:
    out, rest = [], text
    for word in bold:
        before, _sep, rest = rest.partition(word)
        out += [before, word]
    return [*out, rest]


def docx(*paragraphs: str) -> bytes:
    body = "".join(paragraphs)
    return _zip(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>",
        }
    )


def _shape(kind: str | None, texts: list[str], y: int = 0, *, bold: str = "") -> str:
    place = f'<p:ph type="{kind}"/>' if kind else ""
    paras = ""
    for t in texts:
        if bold and bold in t:
            before, _s, after = t.partition(bold)
            strong = f'<a:r><a:rPr b="1"/><a:t>{bold}</a:t></a:r>'
            runs = f"<a:r><a:t>{before}</a:t></a:r>{strong}<a:r><a:t>{after}</a:t></a:r>"
        else:
            runs = f"<a:r><a:t>{t}</a:t></a:r>"
        paras += f"<a:p>{runs}</a:p>"
    return (
        f"<p:sp><p:nvSpPr><p:cNvPr id='1' name=''/><p:cNvSpPr/><p:nvPr>{place}</p:nvPr></p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="0" y="{y}"/></a:xfrm></p:spPr><p:txBody>{paras}</p:txBody></p:sp>'
    )


def pptx(slides: list[str], order: list[int]) -> bytes:
    """A deck whose slides are listed in `order` (file numbers, from 1), as PowerPoint
    keeps a deck reordered: the files keep their numbers, the list says the order."""
    parts: dict[str, str | bytes] = {}
    for n, shapes in enumerate(slides, start=1):
        parts[f"ppt/slides/slide{n}.xml"] = (
            f"<p:sld {PML}><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>"
        )
    ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>' for n in order)
    parts["ppt/presentation.xml"] = f"<p:presentation {PML}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>"
    rels = "".join(
        f'<Relationship Id="rId{n}" Target="slides/slide{n}.xml" Type="slide"/>'
        for n in range(1, len(slides) + 1)
    )
    parts["ppt/_rels/presentation.xml.rels"] = (
        f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'
    )
    return _zip(parts)


def odf(kind: str, body: str, styles: str = "") -> bytes:
    content = (
        f"<office:document-content {ODF}><office:automatic-styles>{styles}</office:automatic-styles>"
        f"<office:body>{body}</office:body></office:document-content>"
    )
    return _zip({"mimetype": f"application/vnd.oasis.opendocument.{kind}", "content.xml": content})


def _cards(found: notefiles.Extracted) -> list[tuple[str, str, str]]:
    return [(c.kind, c.front, c.back) for c in cardmaker.read(found.text)]


def test_a_word_document_keeps_its_headings_items_and_words_in_bold():
    found = notefiles.text(
        "notes.docx",
        docx(
            _para("Linear algebra", style="Heading1"),
            _para("An eigenvalue of A is a number λ such that Av = λv.", bold=("eigenvalue",)),
            _para("Rank: the dimension of the column space."),
            _para("Properties of determinants", bold=("Properties of determinants",)),
            _para("multilinear in the rows", listed=True),
            _para("alternating", listed=True),
            _para("Teoremi", style="Titolo2"),  # Word's Italian style names
        ),
    )
    assert found.kind == "docx"
    assert found.text.splitlines() == [
        "# Linear algebra",
        "An **eigenvalue** of A is a number λ such that Av = λv.",
        "Rank: the dimension of the column space.",
        "",
        "## Properties of determinants",  # a whole line in bold, short: a heading
        "- multilinear in the rows",
        "- alternating",
        "",
        "## Teoremi",
    ]
    assert _cards(found) == [
        ("cloze", "An […] of A is a number λ such that Av = λv", "eigenvalue"),
        ("term", "Rank", "the dimension of the column space"),
        ("list", "Properties of determinants (2)", "multilinear in the rows; alternating"),
    ]


def test_a_deck_is_read_in_its_own_order_titles_over_their_points():
    slides = [
        _shape("title", ["Diffusion"])
        + _shape("body", ["Osmosis is the diffusion of water."], 2000, bold="Osmosis"),
        _shape("ctrTitle", ["The cell membrane"])
        + _shape("body", ["a phospholipid bilayer", "proteins that carry ions"], 2000)
        + _shape("sldNum", ["7"], 9000),
        # Text boxes placed by hand: the topmost short one is the title.
        _shape(None, ["Active transport: against a gradient, using ATP."], 3000)
        + _shape(None, ["Transport"], 100),
    ]
    found = notefiles.text("cells.pptx", pptx(slides, order=[2, 1, 3]))
    assert (found.kind, found.parts, found.read) == ("pptx", 3, 3)
    assert found.text.splitlines() == [
        "# The cell membrane",
        "- a phospholipid bilayer",
        "- proteins that carry ions",
        "",
        "# Diffusion",
        "- **Osmosis** is the diffusion of water.",
        "",
        "# Transport",
        "- Active transport: against a gradient, using ATP.",
    ]
    assert ("list", "The cell membrane (2)", "a phospholipid bilayer; proteins that carry ions") in _cards(
        found
    )
    assert ("cloze", "[…] is the diffusion of water", "Osmosis") in _cards(found)
    assert "7" not in found.text.split()  # the slide number


def test_an_opendocument_text_and_deck():
    styles = (
        '<style:style style:name="P1" style:family="paragraph" style:parent-style-name="Heading_20_1"/>'
        '<style:style style:name="T1" style:family="text">'
        '<style:text-properties fo:font-weight="bold"/></style:style>'
    )
    text = odf(
        "text",
        '<office:text><text:p text:style-name="P1">Genetics</text:p>'
        '<text:p>A <text:span text:style-name="T1">gene</text:span> is a unit of heredity.</text:p>'
        '<text:h text:outline-level="2">Bases</text:h>'
        "<text:list><text:list-item><text:p>adenine</text:p></text:list-item>"
        "<text:list-item><text:p>thymine</text:p></text:list-item></text:list></office:text>",
        styles,
    )
    found = notefiles.text("genetics.odt", text)
    assert found.kind == "odt"
    assert found.text.splitlines() == [
        "# Genetics",  # a heading changed by hand: its style's parent is the heading's
        "A **gene** is a unit of heredity.",
        "",
        "## Bases",
        "- adenine",
        "- thymine",
    ]
    deck = odf(
        "presentation",
        '<office:presentation><draw:page><draw:frame presentation:class="outline" svg:y="5cm"><draw:text-box>'
        "<text:list><text:list-item><text:p>adenine</text:p></text:list-item>"
        "<text:list-item><text:p>guanine</text:p></text:list-item></text:list></draw:text-box></draw:frame>"
        '<draw:frame presentation:class="title" svg:y="1cm"><draw:text-box><text:p>Purines</text:p>'
        '</draw:text-box></draw:frame><presentation:notes><draw:frame presentation:class="notes">'
        "<draw:text-box><text:p>Say this slowly.</text:p></draw:text-box></draw:frame></presentation:notes>"
        "</draw:page></office:presentation>",
    )
    found = notefiles.text("purines.odp", deck)
    assert (found.kind, found.parts) == ("odp", 1)
    assert found.text.splitlines() == ["# Purines", "- adenine", "- guanine"]  # not the speaker's notes


def test_a_pdf_and_a_text_file():
    found = notefiles.text(
        "notes.pdf", pdf([["Rank: the dimension of the column space.", "  - alternating"]])
    )
    assert found.kind == "pdf" and found.parts == found.read == 1
    assert found.text.splitlines() == ["Rank: the dimension of the column space.", "- alternating"]
    # Word's round bullet in a PDF is a symbol font's character, U+F0B7 (as in a PDF
    # LibreOffice wrote); the test's PDF draws Helvetica only, so the step is shown alone.
    assert notefiles._done("\uf0b7 alternating", "pdf", 1, 1, indent=False).text == "- alternating"
    found = notefiles.text("notes.md", "\ufeffQ: What is a gene?\r\nA: A unit of heredity.\r\n".encode())
    assert found.kind == "text" and _cards(found) == [("qa", "What is a gene?", "A unit of heredity")]
    # A text file not in UTF-8, as Windows' Notepad saved them: read as Windows' Latin.
    found = notefiles.text("vocabulaire.txt", "Équation : égalité entre deux expressions".encode("cp1252"))
    assert found.text == "Équation : égalité entre deux expressions"


def test_long_notes_are_cut_and_say_so():
    long = "\n".join(f"Term {n}: a definition of term {n}." for n in range(4000)).encode()
    found = notefiles.text("long.txt", long)
    assert found.cut and len(found.text) == notefiles.MAX_CHARS
    _text, note = service.notes_file_text("long.txt", long)
    assert note is not None and "first" in note


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("photo.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 100, "photo"),
        ("scan.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100, "photo"),
        ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100, "old_office"),
        ("book.epub", _zip({"mimetype": "application/epub+zip", "content.opf": "<package/>"}), "unknown"),
        ("data.bin", b"\x01\x02\x00\x03" * 100, "unknown"),
        ("big.txt", b"a" * (notefiles.MAX_BYTES + 1), "large"),
        ("empty.docx", docx(), "empty"),
        ("broken.docx", _zip({"word/document.xml": "<w:document"}), "unreadable"),
        ("cut.docx", b"PK\x03\x04" + b"\x00" * 50, "unreadable"),
        (
            "entities.docx",
            _zip({"word/document.xml": '<!DOCTYPE d [<!ENTITY a "aaaa">]><d>&a;</d>'}),
            "unreadable",
        ),
    ],
)
def test_what_is_not_read_is_refused_with_why(name, data, reason):
    with pytest.raises(notefiles.NotesError) as refused:
        notefiles.text(name, data)
    assert refused.value.reason == reason
    with pytest.raises(service.InvalidInput):
        service.notes_file_text(name, data)


def test_a_part_that_swells_unzipped_is_refused():
    """A few kilobytes that unzip to more than the limit: read up to it, then refused."""
    bomb = _zip({"word/document.xml": b"<w:document>" + b" " * (notefiles.MAX_UNZIPPED + 10)})
    assert len(bomb) < 100_000
    with pytest.raises(notefiles.NotesError) as refused:
        notefiles.text("bomb.docx", bomb)
    assert refused.value.reason == "large"


@pytest.fixture
def web(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from cps.web import Config, create_app

    config = Config(store=tmp_path, clock=lambda: NOW, background=False, sweep_every=None, remind_every=None)
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture
def token(tmp_path):
    sub = service.new_subscription(
        subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
        start=date(2026, 9, 29),
        tz="Europe/Paris",
        ics=(EXAMPLES / "sample-semester.ics").read_bytes(),
        preferences=service.DEFAULT_PREFERENCES,
        now=NOW,
    )
    service.save_subscription(tmp_path, service.finish_first_run(sub))
    return sub.token


def test_flashcards_send_a_file_and_offer_its_cards(web, tmp_path, token):
    page = unescape(web.get(f"/p/{token}/cards").text)
    assert "Make cards from your notes" in page
    form = page[page.index('class="notes-upload"') - 120 :]
    assert 'enctype="multipart/form-data"' in form and 'type="file" name="file"' in form
    notes = docx(
        _para("Rank: the dimension of the column space."),
        _para("An eigenvalue of A is a number λ such that Av = λv.", bold=("eigenvalue",)),
    )
    read = web.post(
        f"/p/{token}/cards/make",
        data={"action": "read", "course": "Algebra 3"},
        files={"file": ("lecture 3.docx", notes, "application/octet-stream")},
    )
    shown = unescape(read.text)
    assert read.status_code == 200 and "Cards found" in shown
    assert "the dimension of the column space" in shown and "eigenvalue" in shown
    # The text read is on the page, to change; the file itself is kept nowhere.
    assert "Rank: the dimension of the column space." in shown
    assert not any(p.suffix == ".docx" for p in Path(tmp_path).rglob("*"))
    # Adding the ticked cards works as for pasted notes.
    fronts = [c.front for c in cardmaker.read("Rank: the dimension of the column space.")]
    added = web.post(
        f"/p/{token}/cards/make",
        data={
            "action": "add",
            "course": "Algebra 3",
            "front": fronts,
            "back": ["the dimension of the column space"],
            "pick": ["0"],
        },
        follow_redirects=False,
    )
    assert added.status_code == 303 and "added=1" in added.headers["location"]


def test_a_refused_file_says_why_on_the_page(web, token):
    answer = web.post(
        f"/p/{token}/cards/make",
        data={"action": "read"},
        files={"file": ("board.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg")},
    )
    assert answer.status_code == 400 and "Photos of notes are not read yet" in unescape(answer.text)
    too_big = web.post(
        f"/p/{token}/cards/make",
        data={"action": "read"},
        files={
            "file": ("big.pdf", b"%PDF-1.4\n" + b"0" * (notefiles.MAX_BYTES + 300_000), "application/pdf")
        },
    )
    assert too_big.status_code == 413 and "larger than 10 MB" in unescape(too_big.text)
