"""
A course's materials, read from where its teacher keeps them (DECISIONS.md D174).

The owner, about their university's Moodle: "not all courses are managed the same way
by teachers, so it will need to be handled situation by situation". One semester's six
courses showed six ways:

* sections by kind, their files numbered: "L3. Compactness" under Lectures,
  "3. Compactness SOLVED" under Tutorials, the mock exam under Exams;
* a section per session, its slides and a folder of references in each;
* subsections, one of them dated: "Session 1 (18 Nov.)";
* one link to the teacher's own site, whose programme is a table of dated classes,
  each with its exercises and solutions;
* a reading list in a shared document, week by week ("Week four … Oct 8th");
* nothing at all.

So two readers give one result:

* `moodle` reads a course page saved from Moodle 4 (Ctrl+S in the browser): its
  sections and subsections in order, each activity's kind, name and address;
* `page` reads any other page, a teacher's site or a shared document: its headings,
  table rows, paragraphs and items, each link under the week, lecture or day the text
  gave last.

What places an item is read the way `syllabus` reads a programme: a day ("Session 1
(18 Nov.)"), a week ("Week 3", "Week four"), a lecture ("L3.", "Lecture 3", "Session 3",
"Tutorial 3", "3. …", "Econ_AI_03"), from the item's own name first, then its
subsection's, then its section's. What names none is for the whole course; what names an
exam, a mock or a quiz is exam practice. `syllabus.place` then puts each placed item on a
teaching week of the student's timetable.

Pure functions. The page is read for its items' names and addresses, never rendered nor
kept; the addresses open in the student's own browser, where Moodle asks for its own
sign-in. Rejected: downloading the files with the student's Moodle session (a password
or a key the app would have to hold, D92), and a script run on Moodle's pages (code of
ours inside the university's site).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from . import syllabus

MAX_ITEMS = 200  # one course's materials: the largest of the six courses had 79
MAX_CHARACTERS = 4_000_000  # a saved course page: the largest of the six was 363 KB
KINDS = ("file", "folder", "page", "link", "quiz")
SHELVES = ("course", "exams")

# Moodle's activities that hold material, and the kind each is shown as. Forums,
# assignments (their deadlines come with the calendar, D92), feedback, external tools,
# labels and the rest are left out.
_MOODLE_KINDS = {
    "resource": "file",
    "folder": "folder",
    "page": "page",
    "book": "page",
    "lesson": "page",
    "imscp": "page",
    "url": "link",
    "quiz": "quiz",
    "h5pactivity": "quiz",
}
_FILE_ENDINGS = frozenset(
    [
        "pdf",
        "ppt",
        "pptx",
        "pps",
        "ppsx",
        "key",
        "doc",
        "docx",
        "odt",
        "odp",
        "ods",
        "rtf",
        "txt",
        "md",
        "tex",
        "zip",
        "tar",
        "gz",
        "7z",
        "ipynb",
        "py",
        "r",
        "m",
        "c",
        "cpp",
        "java",
        "csv",
        "xls",
        "xlsx",
        "json",
        "mp4",
        "mp3",
    ]
)

# A week or a lecture spelled out ("Week four", "Semaine trois", "Lezione due"): one to
# twenty in English, French, Italian and Spanish, read as their numbers.
_SPELLED = {
    word: n
    for n, words in enumerate(
        (
            "one un une uno",
            "two deux due dos",
            "three trois tre tres",
            "four quatre quattro cuatro",
            "five cinq cinque cinco",
            "six sei seis",
            "seven sept sette siete",
            "eight huit otto ocho",
            "nine neuf nove nueve",
            "ten dix dieci diez",
            "eleven onze undici once",
            "twelve douze dodici doce",
            "thirteen treize tredici trece",
            "fourteen quatorze quattordici catorce",
            "fifteen quinze quindici quince",
            "sixteen seize sedici dieciseis",
            "seventeen dix-sept diciassette diecisiete",
            "eighteen dix-huit diciotto dieciocho",
            "nineteen dix-neuf diciannove diecinueve",
            "twenty vingt venti veinte",
        ),
        start=1,
    )
    for word in words.split()
}
_UNIT_WORDS = (
    r"weeks?|semaines?|settimana|semanas?|wochen?|lectures?|lessons?|class|sessions?|le[cç]ons?|"
    r"cours|s[eé]ances?|lezioni|lezione|clases?|sitzung"
)
_SPELLED_UNIT = re.compile(
    rf"^((?:{_UNIT_WORDS})\.?\s+)("
    + "|".join(sorted(map(re.escape, _SPELLED), key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)
# A sheet of exercises numbered as the lectures are: "Tutorials 3 - Modes of convergence",
# "TD 2", "Esercitazione 4", "Problem set 5". Its number is read as a lecture's.
_EXERCISES = re.compile(
    r"^(?:tutorials?|tutorats?|td|tp|travaux diriges|exercises?|exercise sheets?|"
    r"problem sets?|problem sheets?|worksheets?|esercitazion[ei]|esercizi|ejercicios|practicas?|"
    r"ubung(?:en|sblatt)?|uebung(?:en)?|"
    r"werkcollege|labs?)\.?\s*(?:n[o°]\.?\s*)?(\d{1,2})(?!\d)",
)
# "3. Accumulation points", "03 - Lambda calculus": a number that orders a section's items.
_LEADING = re.compile(r"^(\d{1,2})\s*[.)–—-]\s+\S")
# A file's name with its number at the end: "Econ_AI_03", "References_03", "tut-03-sol".
_TRAILING = re.compile(
    r"^[^\s]*?[_-](\d{1,2})(?:[_-](?:sol|solved|solutions?|corr|corrige|correction|soluzioni|soluzione))?"
    r"(?:\.\w{2,4})?$"
)
_EXAM = re.compile(
    r"\b(?:exams?|examens?|esami|esame|midterms?|mid-term|partiels?|parziali?|mocks?|quiz+|quizzes|annales|"
    r"past papers?|klausur(?:en)?|tentamens?|examenes|parcial(?:es)?|esoneri|esonero)\b"
)
_GRADES = re.compile(r"^(?:grades?|marks|voti|valutazioni|calificaciones|cijfers|bewertung(?:en)?|noten)\b")
# A part of a page that is about the page, not the course.
_NOISE = re.compile(
    r"^(?:acknowledg|credits|contacts?$|about|thanks|remerciements|ringraziamenti|impressum|legal|privacy|"
    r"mentions legales|colophon)"
)
# A link whose words only say what it is ("exercises", "slides", "solutions"), named
# after the class it belongs to.
_SHORT_WORDS = 3


def folded(text: str) -> str:
    """Lower case, accents removed."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


@dataclass(frozen=True)
class Material:
    """One item of a course's materials: its name, its address, its kind, the section
    it is under, and what places it (a week, a lecture, a day), else its shelf."""

    title: str
    url: str
    kind: str = "link"
    group: str = ""
    week: int | None = None
    lecture: int | None = None
    day: str | None = None
    shelf: str = "course"  # where it stays when nothing places it: the whole course, or "exams"

    @property
    def placed(self) -> bool:
        return self.shelf != "exams" and (self.week is not None or self.lecture is not None or bool(self.day))

    def topic(self) -> syllabus.Topic:
        return syllabus.Topic(self.title, self.week, self.lecture, self.day)


@dataclass(frozen=True)
class Reading:
    """What a page gave: where it came from ("moodle" or "page"), the course's name as
    it says it, Moodle's address and the course's number there (from a Moodle page),
    its items in the page's order, and the links left out because the page did not
    say where they lead (relative links in a page saved without its address)."""

    source: str
    name: str
    items: tuple[Material, ...] = ()
    site: str | None = None
    course_id: int | None = None
    unresolved: int = 0


# --------------------------------------------------------------------------- #
# A page as a tree
# --------------------------------------------------------------------------- #

_VOID = frozenset(
    [
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
_SKIP = frozenset({"script", "style", "noscript", "template", "svg", "math"})


class _Node:
    __slots__ = ("attrs", "children", "parent", "tag")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node | str] = []

    def get(self, name: str) -> str:
        return self.attrs.get(name) or ""

    @property
    def classes(self) -> frozenset[str]:
        return frozenset(self.get("class").split())

    def nodes(self) -> Iterator[_Node]:
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.nodes()

    def text(self, leave: frozenset[str] = frozenset()) -> str:
        """The words inside, without those of elements whose class is in `leave`."""
        out: list[str] = []

        def walk(node: _Node) -> None:
            for child in node.children:
                if isinstance(child, str):
                    out.append(child)
                elif not (leave and child.classes & leave):
                    if child.tag in ("br", "p", "div", "li", "td", "th"):
                        out.append(" ")
                    walk(child)

        walk(self)
        return " ".join("".join(out).split())


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.at = self.root
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skipping or tag in _SKIP:
            self.skipping += tag in _SKIP
            return
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.at)
        self.at.children.append(node)
        if tag not in _VOID:
            self.at = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.skipping:
            self.at.children.append(_Node(tag, {k: v or "" for k, v in attrs}, self.at))

    def handle_endtag(self, tag: str) -> None:
        if self.skipping:
            self.skipping -= tag in _SKIP
            return
        node: _Node | None = self.at
        while node is not None and node is not self.root and node.tag != tag:
            node = node.parent
        if node is not None and node is not self.root and node.parent is not None:
            self.at = node.parent

    def handle_data(self, data: str) -> None:
        if not self.skipping and data:
            self.at.children.append(data)


def _tree(html: str) -> _Node:
    parser = _Tree()
    parser.feed(html[:MAX_CHARACTERS])
    parser.close()
    return parser.root


def _title_of(root: _Node) -> str:
    title = next((n for n in root.nodes() if n.tag == "title"), None)
    return title.text() if title is not None else ""


_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))


def _clean(text: str) -> str:
    text = " ".join(text.translate(_INVISIBLE).split()).strip(" \t-–—:;,·|")
    if len(text) > syllabus.MAX_TITLE:
        text = text[: syllabus.MAX_TITLE - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—") + "…"
    return text


# --------------------------------------------------------------------------- #
# What places an item
# --------------------------------------------------------------------------- #


def _undated(topic: str) -> str:
    """A class's topic without the date written with it: "Tris (5 octobre 2026)" is
    "Tris"; "Oct 8th" alone is nothing."""
    topic = re.sub(
        r"\s*[(\[]([^)\]]*)[)\]]", lambda m: "" if syllabus.day_in(m.group(1)) else m.group(0), topic
    )
    return "" if syllabus.day_in(topic) and len(topic.split()) <= 3 else _clean(topic)


def _spelled(text: str) -> str:
    """ "Week four (AI and moral obligation)" read as "Week 4 (…)"."""
    found = _SPELLED_UNIT.match(text)
    if found is None:
        return text
    return found.group(1) + str(_SPELLED[folded(found.group(2))]) + text[found.end() :]


def _mark(text: str, *, numbered: bool = True) -> tuple[int | None, int | None, str | None] | None:
    """The week, lecture and day a name gives, or None. `numbered` reads a bare number
    that orders a section's items ("3. Compactness") and a file's trailing number
    ("Econ_AI_03") as a lecture's; a section's own name is not read so."""
    text = _spelled(" ".join(text.split()))
    if not text:
        return None
    topic = syllabus.marked(text)
    day = syllabus.day_in(text)
    if topic is not None:
        return topic.week, topic.lecture, topic.day or day
    exercises = _EXERCISES.match(folded(text))
    if exercises is not None:
        return None, int(exercises.group(1)) or None, day
    if numbered:
        for pattern in (_LEADING, _TRAILING):
            found = pattern.match(text)
            if found is not None and int(found.group(1)) > 0:
                return None, int(found.group(1)), day
    if day is not None:
        return None, None, day
    return None


def _exam(text: str) -> bool:
    return _EXAM.search(folded(text)) is not None


def _kind_of(url: str) -> str:
    parts = urlsplit(url)
    ending = parts.path.rsplit(".", 1)[-1].lower() if "." in parts.path.rsplit("/", 1)[-1] else ""
    if ending in _FILE_ENDINGS:
        return "file"
    if (parts.hostname or "").endswith("drive.google.com") and "/folders/" in parts.path:
        return "folder"
    return "link"


def _placed(item: Material, names: list[str], *, numbered: bool) -> Material:
    """The item with what places it. A section or subsection that gives a day or a week
    is the course's calendar ("28 settembre - 4 ottobre", "Week 3"), and outranks the
    item's own number ("Esercitazione 2" in the week of 5 October is that week's);
    else the item's own name ("L3.", "Econ_AI_03"); else a section's number ("Session
    3"). Exam practice stays on its shelf."""
    if _exam(item.title) or any(_exam(n) for n in names):
        return replace(item, shelf="exams")
    above = [m for m in (_mark(n, numbered=False) for n in names) if m is not None]
    dated = next((m for m in above if m[0] is not None or m[2] is not None), None)
    mark = dated or _mark(item.title, numbered=numbered) or (above[0] if above else None)
    if mark is None:
        return item
    week, lecture, day = mark
    return replace(item, week=week, lecture=lecture, day=day)


# --------------------------------------------------------------------------- #
# A course page saved from Moodle
# --------------------------------------------------------------------------- #


def _section(node: _Node) -> tuple[str, int | None] | None:
    """A Moodle section's name and number, or None for anything else."""
    if node.tag != "li" or "section" not in node.classes or not node.get("id").startswith("section-"):
        return None
    name = node.get("data-sectionname") or node.get("aria-label")
    if not name:
        heading = next(
            (n for n in node.nodes() if n.tag in ("h3", "h4") and "sectionname" in n.classes), None
        )
        name = heading.text() if heading is not None else ""
    number = node.get("data-number") or node.get("id").removeprefix("section-")
    return _clean(name), int(number) if number.isdigit() else None


def _modtype(node: _Node) -> str | None:
    if node.tag != "li" or "activity" not in node.classes:
        return None
    return next((c.removeprefix("modtype_") for c in node.classes if c.startswith("modtype_")), None)


def _activity_name(node: _Node) -> str:
    named = next((n for n in node.nodes() if n.get("data-activityname")), None)
    if named is not None:
        return named.get("data-activityname")
    instance = next((n for n in node.nodes() if "instancename" in n.classes), None)
    return instance.text(frozenset({"accesshide"})) if instance is not None else ""


def moodle(html: str) -> Reading | None:
    """A course page saved from Moodle: each activity that holds material, under its
    section and subsection, placed by their names. None when the page is not one."""
    root = _tree(html)
    if not any(_modtype(n) for n in root.nodes()):
        return None
    found = re.search(r'"wwwroot"\s*:\s*"(https?:[^"]+)"', html)
    site = found.group(1).replace("\\/", "/").rstrip("/") if found else None
    body = next((n for n in root.nodes() if n.tag == "body"), None)
    number = re.search(r"\bcourse-(\d+)\b", body.get("class")) if body is not None else None
    title = _title_of(root)
    name = _clean(re.sub(r"\s*\|\s*[^|]*$", "", title.split(":", 1)[-1] if ":" in title else title))
    items: list[Material] = []
    last: list[tuple[str, int]] = []  # the unit word and number of the last numbered section

    def marking(name: str) -> str:
        """A section's name as it is read for placing: one that goes on from numbered
        ones without its own number ("Session: AI as a GPT" after "Session 6") is the
        next ("Session 7: AI as a GPT")."""
        topic = syllabus.marked(_spelled(name))
        word = re.match(r"^([^\W\d_]+)", name)
        if topic is not None and (topic.week or topic.lecture) and word:
            last[:] = [(folded(word.group(1)), topic.week or topic.lecture or 0)]
            return name
        if last and word and folded(word.group(1)) == last[0][0] and not re.search(r"\d", name):
            number = last[0][1] + 1
            last[:] = [(last[0][0], number)]
            return f"{word.group(1)} {number}: {name[word.end() :].strip(' :-–—')}"
        return name

    def walk(node: _Node, path: tuple[tuple[str, int | None, str], ...]) -> None:
        for child in node.children:
            if not isinstance(child, _Node):
                continue
            section = _section(child)
            if section is not None:
                name, number = section
                walk(child, (*path, (name, number, marking(name) if not path else name)))
                continue
            modtype = _modtype(child)
            if modtype is None:
                walk(child, path)
                continue
            if modtype == "subsection":
                walk(child, path)  # its section, inside it, names its items
                continue
            kind = _MOODLE_KINDS.get(modtype)
            link = next(
                (n for n in child.nodes() if n.tag == "a" and f"/mod/{modtype}/" in n.get("href")), None
            )
            title = _clean(_activity_name(child))
            if kind is None or link is None or not title:
                continue
            url = link.get("href")
            if not re.match(r"^https?://", url, re.IGNORECASE):
                url = urljoin(site + "/", url) if site else ""
            if not url or _GRADES.match(folded(title)) or any(_GRADES.match(folded(n)) for n, _, _ in path):
                continue
            general = bool(path) and path[0][1] == 0
            names = [m for _, number, m in reversed(path) if m and number != 0]
            group = next((n for n, _, _ in reversed(path) if n), "")
            item = Material(title, url, kind, group)
            items.append(_placed(item, names, numbered=not general))

    walk(root, ())
    return Reading(
        "moodle",
        name,
        tuple(_unique(items)),
        site,
        int(number.group(1)) if number else None,
    )


def _unique(items: list[Material]) -> list[Material]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item.url not in seen:
            seen.add(item.url)
            out.append(item)
    return out[:MAX_ITEMS]


# --------------------------------------------------------------------------- #
# Any other page: a teacher's site, a shared document
# --------------------------------------------------------------------------- #

_BLOCK_TAGS = frozenset(
    [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "tr",
        "dt",
        "dd",
        "blockquote",
        "pre",
        "caption",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "main",
        "nav",
        "aside",
    ]
)


@dataclass
class _Block:
    tag: str
    words: list[str]
    pieces: list[tuple[str, str]]  # in order: ("text", words) and ("link", words + "\x00" + address)
    cells: list[list[str]]  # a row's cells' words
    link_words: list[str]
    hidden: bool = False  # inside a page's navigation or footer

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.words).split())

    @property
    def links(self) -> list[tuple[str, str]]:
        return [tuple(p[1].split("\x00", 1)) for p in self.pieces if p[0] == "link"]  # type: ignore[misc]


_PLAIN_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"]+[^\s<>\".,;:)\]]")


def _blocks(root: _Node) -> list[_Block]:
    blocks: list[_Block] = []

    def text_piece(block: _Block, text: str) -> None:
        """A run of words, an address written in it made a link of its own."""
        at = 0
        for found in _PLAIN_URL.finditer(text):
            block.pieces.append(("text", text[at : found.start()]))
            address = found.group(0) if found.group(0).startswith("http") else "https://" + found.group(0)
            block.pieces.append(("link", found.group(0) + "\x00" + address))
            at = found.end()
        block.pieces.append(("text", text[at:]))

    def walk(node: _Node, block: _Block | None, cell: list[str] | None, hidden: bool) -> None:
        for child in node.children:
            if isinstance(child, str):
                if block is not None:
                    block.words.append(child)
                    text_piece(block, child)
                    if cell is not None:
                        cell.append(child)
                continue
            if child.tag in ("head", "title"):
                continue
            inside = hidden or child.tag in ("nav", "footer") or child.get("role") == "navigation"
            if child.tag in _BLOCK_TAGS:
                inner = _Block(child.tag, [], [], [], [], inside)
                blocks.append(inner)
                walk(child, inner, None, inside)
                if block is not None:
                    block.words.append(" ")
                continue
            if block is not None and block.tag == "tr" and child.tag in ("td", "th"):
                new_cell: list[str] = []
                block.cells.append(new_cell)
                block.words.append(" ")
                walk(child, block, new_cell, inside)
                block.words.append(" ")
                continue
            if child.tag == "a" and child.get("href") and block is not None:
                words = child.text()
                block.pieces.append(("link", words + "\x00" + child.get("href")))
                block.link_words.append(words)
                block.words.append(" " + words + " ")
                if cell is not None:
                    cell.append(" " + words + " ")
                continue
            if child.tag == "br" and block is not None:
                block.words.append(" ")
                block.pieces.append(("text", " "))
            walk(child, block, cell, inside)

    walk(root, None, None, False)
    return [b for b in blocks if not b.hidden and (b.text or b.links)]


def _address(href: str, base: str | None) -> str | None:
    """A link's full address: relative ones against the page's own, Google's redirect
    unwrapped; None for one that leads nowhere this app opens."""
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    if not re.match(r"^https?://", href, re.IGNORECASE):
        if base is None:
            return None
        href = urljoin(base, href)
    parts = urlsplit(href)
    if (parts.hostname or "").endswith("google.com") and parts.path == "/url":
        target = parse_qs(parts.query).get("q", [""])[0]
        return target if re.match(r"^https?://", target, re.IGNORECASE) else None
    return href if parts.scheme in ("http", "https") and parts.hostname else None


_LABELS = re.compile(
    r"^(?:primary|secondary|required|recommended|optional|further|essential|core|"
    r"obligatoire|facultatif|complementaire|obbligatori[oa]|facoltativ[oa]|consigliat[oa])"
    r"(?:\s+(?:readings?|lectures?|letture))?\s*[:.-]?\s+",
    re.IGNORECASE,
)


_ADDRESS_WORDS = re.compile(r"^(?:https?://|www\.|[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/)\S*$", re.IGNORECASE)


def _named(words: str, context: str, before: str) -> str:
    """A link's name: its words, after its class's topic when they only say what it is
    ("Turing machines · exercises"); the words written before it when they are an
    address ("Nagel, T. (1974). What is it like to be a bat? https://…")."""
    words = _clean(words.strip().strip("()[]"))
    if not words or _ADDRESS_WORDS.match(words):
        return _clean(_LABELS.sub("", " ".join(before.split()))) or words
    if context and len(words.split()) <= _SHORT_WORDS:
        return _clean(f"{context} · {words}")
    return words


def _row_topic(block: _Block) -> str:
    """A table row's topic: its longest cell that is neither its number, its day nor
    only links."""
    best = ""
    for cell in block.cells:
        text = " ".join(" ".join(cell).split())
        for words in block.link_words:
            if words.strip():
                text = text.replace(words.strip(), " ")
        text = " ".join(re.sub(r"\(\s*\)|\[\s*\]", " ", text).split()).strip(" .,;:·-")
        text = re.sub(r"\s+([.,;:])", r"\1", text)
        mark = syllabus.marked(text) if text else None
        if not text or (mark is not None and not mark.title):
            continue
        if len(text.split()) > len(best.split()):
            best = text
    return _clean(best)


def page(html: str, base: str | None = None) -> Reading:
    """Any page with a course's materials: each link under the week, lecture or day the
    text gave last (a heading, a table's row, a short line), else for the whole course.
    `base` is the page's own address, for its relative links; a page saved without it
    loses those, counted in `unresolved`."""
    root = _tree(html)
    title = _clean(_title_of(root))
    items: list[Material] = []
    unresolved = 0
    unit: tuple[int | None, int | None, str | None] | None = None
    unit_topic = ""
    unit_level = 99
    group = ""
    begun = False  # past the page's own title and introduction
    orphan = ""  # the last paragraph without a link: a reference whose address comes next
    for block in _blocks(root):
        text = _spelled(block.text)
        level = int(block.tag[1]) if re.fullmatch(r"h[1-6]", block.tag) else None
        row_unit = None
        context = unit_topic
        if level is not None:
            topic = syllabus.marked(text)
            if topic is not None:
                unit = (topic.week, topic.lecture, topic.day or syllabus.day_in(text))
                unit_topic, unit_level, group, begun = _undated(topic.title), level, text, True
            elif level <= unit_level or unit is None:
                unit, unit_topic, unit_level = None, "", 99
                group = _clean(text)
                begun = begun or level > 1
            context = unit_topic
        elif block.tag == "tr":
            cells = [" ".join(" ".join(c).split()) for c in block.cells]
            topic = syllabus.marked(_spelled(" ".join(c for c in cells[:3] if c)))
            if topic is not None:
                row_unit = (topic.week, topic.lecture, topic.day)
                context = _row_topic(block)
                begun = True
        else:
            topic = syllabus.marked(text) if len(text.split()) <= 12 else None
            if topic is not None and (topic.week is not None or topic.lecture is not None):
                unit = (topic.week, topic.lecture, topic.day or syllabus.day_in(text))
                unit_topic, unit_level, begun = _undated(topic.title), 7, True
                context = unit_topic
            elif unit is not None and unit[2] is None and len(text.split()) <= 4 and syllabus.day_in(text):
                unit = (unit[0], unit[1], syllabus.day_in(text))
        if _NOISE.match(folded(group)):
            continue
        before = "" if block.text.strip() != "".join(w for w, _ in block.links).strip() else orphan
        orphan = "" if block.links else block.text
        for piece, value in block.pieces:
            if piece == "text":
                before += value
                continue
            words, href = value.split("\x00", 1)
            url = _address(href, base)
            if url is None:
                unresolved += not re.match(r"^(?:#|mailto:|javascript:|tel:|data:)", href.strip())
                continue
            here = row_unit or unit
            kind = _kind_of(url)
            name = _named(words, context, before)
            before = ""
            if (here is None and not begun and kind == "link") or not name:
                continue  # the page's own introduction: its authors' pages, a wiki entry
            item = Material(name, url, kind, group if here is None else "")
            if _exam(name) or (row_unit is None and unit is None and _exam(group)):
                items.append(replace(item, shelf="exams"))
            elif here is not None:
                items.append(replace(item, week=here[0], lecture=here[1], day=here[2]))
            else:
                items.append(item)
    return Reading("page", title, tuple(_unique(items)), unresolved=unresolved)


def read(html: str, base: str | None = None) -> Reading:
    """A course page saved from Moodle, else any other page. Another of Moodle's pages
    (the list of courses, the dashboard) gives no items: its links are Moodle's own."""
    found = moodle(html)
    if found is not None:
        return found
    if re.search(r'"wwwroot"\s*:\s*"https?:', html) and "M.cfg" in html:
        return Reading("moodle", _clean(_title_of(_tree(html))))
    return page(html, base)
