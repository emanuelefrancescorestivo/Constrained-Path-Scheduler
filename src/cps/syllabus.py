"""
A course's syllabus, pasted as text, read into topics (DECISIONS.md D81).

The student copies the course's program from its page or its PDF and pastes it;
rules, not a model, cut it into topics (docs/CURRICULUM.md §3a). Each topic keeps
its week ("Week 3: ...", "Semaine 3", "Settimana 3", "Semana 3", "Woche 3") or its
lecture ("Lecture 5 - ...", "Cours 5", "Lezione 5", "Clase 5", "Vorlesung 5") when
the text says so; a line under "Week 3" belongs to week 3 until the next one says
otherwise. Bullets and numbering are dropped, headings ("Contents:", "Programma")
and links skipped. A list that names no week at all is spread over the course's
teaching weeks in order, a week with more lecture hours taking more (`place`).

What it cannot read: a table pasted as one line, prose, dates (a date is kept in
the title, not placed). The student sees each topic with its week before saving.

Pure functions; `cps.service` keeps the topics and finds the weeks in the timetable.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

MAX_TOPICS = 60  # a semester's course rarely lists more; the rest is not kept
MAX_TITLE = 120  # characters: a topic, not a paragraph

# Words that number a week or a lecture: English, French, Italian, Spanish and
# Portuguese, German, Dutch. "Les" (Dutch for a lesson) is left out: "Les 3 théorèmes" is
# French for "the 3 theorems". A single letter ("W3", "L5") counts only before a
# colon, a dash or a stop: "L1 norm" is a topic.
_WEEK = r"weeks?|wk|semaines?|settimana|settimane|sett|semanas?|wochen?|w"
_LECTURE = (
    r"lectures?|lect?|lessons?|class|sessions?|le[cç]ons?|cours|s[eé]ances?|lezioni|lezione|lez|"
    r"clases?|aulas?|vorlesung(?:en)?|vl|sitzung|hoorcollege|college|l"
)
_MARKER = re.compile(
    rf"^(?:(?P<week>{_WEEK})|(?P<lecture>{_LECTURE}))\.?\s*(?:n[o°º]\.?\s*)?(?P<n>\d{{1,2}})(?!\d)"
    r"(?:\s*[-–]\s*\d{1,2}(?!\d))?"  # "Weeks 3-4": the first
    r"(?:\s*\([^)]*\))?"  # "Week 3 (12/10)"
    r"(?P<sep>\s*[:.)\]·|–—-]\s*|\s+|$)(?P<rest>.*)$",
    re.IGNORECASE,
)
# A bullet, or numbering that only orders the list: "-", "•", "2.", "2)", "(2)",
# "1.1", "a)", "iv.". A bare "3 laws of motion" keeps its number.
_BULLET = re.compile(
    r"^\s*(?:[-*•▪◦●■□➢►–—·+]+|\d{1,3}(?:\.\d{1,3})+\.?(?=\s)|\(?\d{1,3}[.)]|[a-zA-Z][.)](?=\s)|"
    r"[ivxIVX]{1,4}[.)](?=\s))\s*"
)
_INLINE_BULLETS = re.compile(r"\s*[•▪◦●■]\s*")
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
# Lines that only name the list, accents removed, lower case.
_HEADINGS = frozenset(
    {
        "syllabus",
        "program",
        "programme",
        "programma",
        "programa",
        "contents",
        "content",
        "contenu",
        "contenus",
        "contenuti",
        "contenidos",
        "temario",
        "inhalt",
        "inhalte",
        "topics",
        "outline",
        "course outline",
        "course content",
        "course contents",
        "schedule",
        "calendar",
        "calendrier",
        "calendario",
        "argomenti",
        "plan du cours",
        "programma del corso",
        "contenuti del corso",
    }
)
# A pasted table's header row: every cell one of these words.
_COLUMNS = frozenset(
    {
        "week",
        "lecture",
        "topic",
        "topics",
        "date",
        "day",
        "content",
        "reading",
        "readings",
        "semaine",
        "seance",
        "cours",
        "sujet",
        "theme",
        "lectures",
        "settimana",
        "lezione",
        "data",
        "argomento",
        "argomenti",
        "semana",
        "clase",
        "fecha",
        "tema",
        "woche",
        "vorlesung",
        "datum",
        "thema",
    }
)


@dataclass(frozen=True)
class Topic:
    title: str
    week: int | None = None  # the course's teaching week, from 1, when the text says it
    lecture: int | None = None  # the course's lecture, from 1, when the text says it

    def to_dict(self) -> dict:
        return {"title": self.title, "week": self.week, "lecture": self.lecture}

    @classmethod
    def from_dict(cls, data: dict) -> Topic:
        return cls(str(data["title"]), data.get("week"), data.get("lecture"))


def _folded(text: str) -> str:
    """Lower case, accents removed: "Leçon" and "lecon" are one word here."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


def _title(text: str) -> str:
    text = " ".join(text.split()).strip(" \t-–—:;,.·|")
    if len(text) > MAX_TITLE:
        text = text[: MAX_TITLE - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—") + "…"
    return text


def _lines(text: str) -> list[str]:
    """The text's lines, cells of a pasted table joined by " · ", bullets run
    together on one line split."""
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"\t+", " · ", raw.strip()).strip(" ·")
        if len(_INLINE_BULLETS.findall(line)) >= 2:
            out += [part for part in _INLINE_BULLETS.split(line) if part.strip()]
        else:
            out.append(line)
    return out


def read(text: str) -> list[Topic]:
    """The topics of a pasted syllabus, in order, at most `MAX_TOPICS`."""
    topics: list[Topic] = []
    week: int | None = None
    lecture: int | None = None
    for line in _lines(text):
        if not line or _LINK.search(line):
            continue
        if all(_folded(cell.strip()) in _COLUMNS for cell in line.split(" · ")):
            continue
        line = _BULLET.sub("", line, count=1)
        marker = _MARKER.match(line)
        word = (marker["week"] or marker["lecture"]) if marker is not None else ""
        if marker is not None and (len(word) > 1 or marker["sep"].strip() or not marker["rest"]):
            n = int(marker["n"])
            week, lecture = (n, None) if marker["week"] else (None, n)
            line = marker["rest"]
        else:
            marker = None
        if _folded(_title(line)) in _HEADINGS or (marker is None and line.rstrip().endswith(":")):
            continue
        title = _title(_BULLET.sub("", line, count=1))
        if sum(c.isalpha() for c in title) < 3:
            continue
        if topics and topics[-1] == Topic(title, week, lecture):
            continue
        topics.append(Topic(title, week, lecture))
        if len(topics) == MAX_TOPICS:
            break
    return topics


def place(
    topics: Sequence[Topic], week_hours: Sequence[float], lecture_weeks: Sequence[int]
) -> list[int | None]:
    """The teaching week (from 1) of each topic, or None when it has none.

    `week_hours` are the lecture hours of each of the course's teaching weeks in
    the timetable, in order; `lecture_weeks` the teaching week of each of its
    lectures, in order. A topic's own week or lecture places it; when no topic
    names one, the list is spread over the weeks in order, each week taking a share
    of the topics as large as its share of the hours. A week or lecture beyond the
    timetable places nothing."""
    if any(t.week is not None or t.lecture is not None for t in topics):
        out: list[int | None] = []
        for t in topics:
            if t.week is not None:
                out.append(t.week if 1 <= t.week <= len(week_hours) else None)
            elif t.lecture is not None:
                out.append(lecture_weeks[t.lecture - 1] if 1 <= t.lecture <= len(lecture_weeks) else None)
            else:
                out.append(None)
        return out
    total = sum(week_hours)
    if not topics or total <= 0:
        return [None] * len(topics)
    out = []
    for i in range(len(topics)):
        target, done = (i + 0.5) / len(topics) * total, 0.0
        for k, hours in enumerate(week_hours, start=1):
            done += hours
            if done >= target - 1e-9:
                out.append(k)
                break
    return out


def to_text(topics: Sequence[Topic], week_word: str = "Week", lecture_word: str = "Lecture") -> str:
    """The topics as lines `read` gives back: "Week 3: ...", "Lecture 5: ...", or
    the title alone. `week_word` and `lecture_word` are the student's language's,
    which `read` knows."""
    lines = []
    for t in topics:
        if t.week is not None:
            lines.append(f"{week_word} {t.week}: {t.title}")
        elif t.lecture is not None:
            lines.append(f"{lecture_word} {t.lecture}: {t.title}")
        else:
            lines.append(t.title)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# A whole course page: its sections, its assessment, its credits (D83)
# --------------------------------------------------------------------------- #

# The ECTS Users' Guide (2015, §7.1) recommends the same items for every course
# unit in a catalogue: among them its credits, learning outcomes, prerequisites,
# content, reading, teaching methods and assessment methods; the format is the
# institution's [ref:ects2015]. So a course page usually has these headings, in
# its own words. Each section's headings, accents removed, lower case.
SECTIONS = {
    "content": (
        "course content",
        "course contents",
        "content",
        "contents",
        "syllabus",
        "programme",
        "program",
        "course outline",
        "outline",
        "topics",
        "topics covered",
        "schedule",
        "course schedule",
        "contenu",
        "contenus",
        "contenu du cours",
        "programme du cours",
        "plan du cours",
        "contenuti",
        "contenuti del corso",
        "programma",
        "programma del corso",
        "argomenti",
        "contenidos",
        "temario",
        "programa",
        "conteudos",
        "conteudo programatico",
        "inhalt",
        "inhalte",
        "lehrinhalte",
        "inhoud",
        "leerinhoud",
    ),
    "outcomes": (
        "learning outcomes",
        "intended learning outcomes",
        "objectives",
        "aims",
        "course objectives",
        "goals",
        "acquis d'apprentissage",
        "resultats d'apprentissage",
        "objectifs",
        "competences visees",
        "obiettivi",
        "obiettivi formativi",
        "risultati di apprendimento",
        "risultati di apprendimento attesi",
        "objetivos",
        "resultados de aprendizaje",
        "competencias",
        "lernziele",
        "lernergebnisse",
        "qualifikationsziele",
        "leerdoelen",
        "leerresultaten",
    ),
    "assessment": (
        "assessment",
        "assessment methods",
        "assessment methods and criteria",
        "evaluation",
        "examination",
        "exam",
        "grading",
        "modalites d'evaluation",
        "modalites de controle des connaissances",
        "controle des connaissances",
        "modalita d'esame",
        "modalita di verifica",
        "verifica dell'apprendimento",
        "modalita di verifica dell'apprendimento",
        "valutazione",
        "esame",
        "evaluacion",
        "sistema de evaluacion",
        "avaliacao",
        "prufung",
        "prufungsform",
        "leistungsnachweis",
        "toetsing",
        "beoordeling",
    ),
    "prerequisites": (
        "prerequisites",
        "prerequisites and co-requisites",
        "prerequis",
        "prerequisiti",
        "requisitos",
        "prerrequisitos",
        "voraussetzungen",
        "vereiste voorkennis",
        "ingangseisen",
    ),
    "reading": (
        "reading",
        "recommended reading",
        "required reading",
        "bibliography",
        "textbooks",
        "references",
        "learning resources",
        "bibliographie",
        "ouvrages de reference",
        "testi",
        "testi di riferimento",
        "bibliografia",
        "testi adottati",
        "bibliografia basica",
        "literatur",
        "literatuur",
    ),
    "methods": (
        "teaching methods",
        "planned learning activities and teaching methods",
        "learning activities",
        "methodes d'enseignement",
        "metodi didattici",
        "metodologia",
        "metodologia docente",
        "lehrmethoden",
        "lehr- und lernformen",
        "werkvormen",
    ),
}
_SECTION_OF = {heading: name for name, headings in SECTIONS.items() for heading in headings}
_LONGEST = max(len(h.split()) for h in _SECTION_OF)
CREDIT_HOURS = (25, 30)  # one ECTS credit is 25 to 30 hours of work [ref:ects2015]
_CREDITS = re.compile(
    r"(?:(?<![\d.,])(?P<n>\d{1,2}(?:[.,]5)?)\s*"
    r"(?:ects|cfu|crediti|credits?|credits ects|creditos|creditos ects|lp|leistungspunkte|ec|studiepunten)\b)"
    r"|(?:\b(?:ects|cfu|credits?|crediti|creditos)\b\s*[:=]?\s*(?P<m>\d{1,2}(?:[.,]5)?)(?![\d.,]\d))",
)
_PART = re.compile(
    r"(?P<a>[^\d%,;+()]{3,60}?)\s*\(?\s*(?P<p>\d{1,3})\s*%\s*\)?|\(?\s*(?P<q>\d{1,3})\s*%\s*\)?\s*(?P<b>[^\d%,;+()]{3,60})"
)
# "and" between two parts, in the languages read: dropped from the front of a label.
# Matched only there, never split on: French "en ligne" is part of "examen en ligne".
_AND = re.compile(r"^(?:and|et|e|y|und|en|plus|ed)\s+", re.IGNORECASE)
_NOT_HEADING = re.compile(r"^\s*[-*•▪◦●■□➢►–—·+]")  # a bulleted line is an item, not a heading


def _heading(line: str) -> tuple[str, str] | None:
    """The section a line opens, and what follows the heading on the same line
    ("Assessment: written exam 70 %"), or None for an ordinary line."""
    if _NOT_HEADING.match(line):
        return None
    folded = _folded(_BULLET.sub("", line.strip(), count=1)).strip()
    folded = re.sub(r"^\d{1,2}(?:\.\d{1,2})*\.?\s+", "", folded)  # "3. Course content"
    head, colon, rest = folded.partition(":")
    head = head.strip(" .-–—")
    words = head.split()
    if not words or len(words) > _LONGEST:
        return None
    name = _SECTION_OF.get(head)
    if name is None:
        return None
    if not colon and rest:
        return None
    start = len(line) - len(rest.lstrip()) if colon and rest.strip() else len(line)
    return name, line[start:].strip()


def sections(text: str) -> dict[str, str]:
    """A course page cut at its headings: each section's text, the longest of each
    kind ("Exam: 15 January" above "Assessment methods" and their list keeps the
    list); nothing when the text has none of the headings."""
    found: list[tuple[str, list[str]]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = _heading(line)
        if heading is not None:
            name, rest = heading
            found.append((name, [rest] if rest else []))
        elif found:
            found[-1][1].append(line)
    out: dict[str, str] = {}
    for name, lines in found:
        body = "\n".join(lines).strip()
        if len(body) > len(out.get(name, "")):
            out[name] = body
    return out


def course_text(text: str) -> str:
    """The part of a pasted course page that lists its topics: its content section
    when it has one with a topic in it, else all of it."""
    content = sections(text).get("content", "")
    return content if read(content) else text


@dataclass(frozen=True)
class Part:
    """A part of the course's assessment: "Written exam", 70 (percent)."""

    label: str
    percent: int


def assessment(text: str) -> list[Part]:
    """The assessment's parts with their percentages, from the assessment section
    (none without one): "Written exam 70 %, project 30 %", "(70 %) written exam".
    Kept only when they add up to 100, within 1, the way a weighting must."""
    found = sections(text).get("assessment", "")
    parts = []
    for line in found.split("\n"):
        for m in _PART.finditer(line):
            label = _title(_AND.sub("", _title(m["a"] or m["b"] or "")))
            percent = int(m["p"] or m["q"])
            if sum(c.isalpha() for c in label) >= 3 and 0 < percent <= 100:
                parts.append(Part(label[0].upper() + label[1:], percent))
    return parts if parts and abs(sum(p.percent for p in parts) - 100) <= 1 else []


def credits(text: str) -> float | None:
    """The course's ECTS credits (or the national equivalent written beside them:
    CFU, LP, EC), from 1 to 30, or None."""
    for m in _CREDITS.finditer(_folded(text)):
        value = float((m["n"] or m["m"]).replace(",", "."))
        if 1 <= value <= 30:
            return value
    return None


def workload(credit: float) -> tuple[int, int]:
    """The hours of work the credits stand for, lectures included [ref:ects2015]."""
    return round(credit * CREDIT_HOURS[0]), round(credit * CREDIT_HOURS[1])
