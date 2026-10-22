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

A schedule led by dates or by week numbers is read row by row, and a whole
semester's program is cut into its courses (D90, below). Prose is skipped: what it
cannot read is a list written as sentences, or a table pasted as one line. The
student sees each topic with its week before saving.

Pure functions; `cps.service` keeps the topics and finds the weeks in the timetable.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

MAX_TOPICS = 60  # a semester's course rarely lists more; the rest is not kept
MAX_TITLE = 120  # characters: a topic, not a paragraph
KINDS = ("topic", "assessment")

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
    # D90: the day a schedule gives it ("2026-09-15", or "09-15" when the row has no
    # year), what to read for it, and "assessment" for a mock exam, a midterm, a quiz.
    day: str | None = None
    material: str | None = None
    kind: str = "topic"

    def to_dict(self) -> dict:
        out: dict = {"title": self.title, "week": self.week, "lecture": self.lecture}
        if self.day:
            out["day"] = self.day
        if self.material:
            out["material"] = self.material
        if self.kind != "topic":
            out["kind"] = self.kind
        return out

    @classmethod
    def from_dict(cls, data: dict) -> Topic:
        kind = data.get("kind") if data.get("kind") in KINDS else "topic"
        return cls(
            str(data["title"]),
            data.get("week"),
            data.get("lecture"),
            data.get("day") or None,
            data.get("material") or None,
            str(kind),
        )


def folded(text: str) -> str:
    """Lower case, accents removed: "Leçon" and "lecon" are one word here."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


_folded = folded


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
    """The topics of a pasted syllabus, in order, at most `MAX_TOPICS`: a dated or
    numbered schedule's rows when it has one (D90), else its list."""
    lines = _lines(text)
    rows = _rows(lines)
    return (rows if rows is not None else _listed(lines))[:MAX_TOPICS]


def _logical(lines: Sequence[str]) -> list[str]:
    """Lines as written, with the lines a PDF wrapped joined back: a line that goes
    on from a long one left open (no stop, no colon) and starts in lower case, or
    follows a full-width line. Bullets and weeks always start a line."""
    longest = max((len(line) for line in lines), default=0)
    out: list[str] = []
    open_, width = False, 0
    for line in lines:
        if not line:
            out.append("")
            open_ = False
            continue
        starts = bool(_BULLET.match(line)) or _marker(_BULLET.sub("", line, count=1)) is not None
        if (
            out
            and out[-1]
            and open_
            and not starts
            and not _LINK.search(line)
            and _date(line) is None
            and (line[0].islower() or width >= max(60, 0.8 * longest))
        ):
            out[-1] += " " + line
        else:
            out.append(line)
        width = len(line)
        open_ = width >= max(35, 0.6 * longest) and not line.rstrip().endswith((".", ":", ";", "!", "?"))
    return out


def _marker(line: str) -> re.Match[str] | None:
    """A week or a lecture that numbers the line ("Week 3:", "Cours 5 -"), or None."""
    marker = _MARKER.match(line)
    if marker is None:
        return None
    word = marker["week"] or marker["lecture"]
    return marker if len(word) > 1 or marker["sep"].strip() or not marker["rest"] else None


def _prose(line: str) -> bool:
    """A paragraph, not a topic: two sentences or more, or a long one."""
    if _BULLET.match(line) or _marker(line) is not None:
        return False
    sentences = len(re.findall(r"[.!?]\s+[A-ZÀ-Ý]", line))
    return sentences >= 1 or len(line.split()) >= 16


def _listed(lines: Sequence[str]) -> list[Topic]:
    topics: list[Topic] = []
    week: int | None = None
    lecture: int | None = None
    logical = _logical(lines)
    for i, line in enumerate(logical):
        if not line or _LINK.search(line) or _prose(line):
            continue
        if all(_folded(cell.strip()) in _COLUMNS for cell in line.split(" · ")):
            continue
        line = _BULLET.sub("", line, count=1)
        marker = _marker(line)
        if marker is not None:
            n = int(marker["n"])
            week, lecture = (n, None) if marker["week"] else (None, n)
            line = marker["rest"]
        elif logical[i] == line and _labels(logical[i + 1 :]):
            continue  # "Continuity and limits" above its bulleted items: a label
        if _folded(_title(line)) in _HEADINGS or (marker is None and line.rstrip().endswith(":")):
            continue
        day = _date(line)
        if day is not None:
            line = line[day[1] :]
        title = _title(_BULLET.sub("", line, count=1))
        if sum(c.isalpha() for c in title) < 3 or (_OFF.search(title) and len(title.split()) <= 5):
            continue  # too short, or a day without class
        topic = Topic(title, week, lecture, day[0] if day else None, kind=_kind(title))
        if topics and topics[-1] == topic:
            continue
        topics.append(topic)
    return topics


def _labels(after: Sequence[str]) -> bool:
    """Whether the next line written opens a bulleted list or a group ("Inequalities:")."""
    for line in after:
        if line:
            return bool(_BULLET.match(line)) or (line.rstrip().endswith(":") and _marker(line) is None)
    return False


# -- a schedule's rows (D90) ---------------------------------------------------------
#
# A program's schedule, pasted or taken from a PDF: one row per class, led by its
# date ("Tue, 15-09-26 1 Groups…", "15 Sept", "2026-09-15") or, under a header that
# names a week column ("Week  Content"), by its week's number. A row's cell wraps
# over the lines below it; what it says to read ("Gallian, Chapter 3") is kept apart;
# "Continuation" and a day without class are not topics; a mock exam, a midterm or a
# quiz is kept as an assessment in its week.

# Weekday words, accents removed, Monday 0: English, French, Italian, Spanish,
# Portuguese, German, Dutch, written out or cut short.
_WEEKDAYS = {
    word: n
    for n, words in enumerate(
        (
            "monday mon lundi lun lunedi lunes segunda seg montag mo maandag ma",
            "tuesday tue tues mardi mar martedi martes terca ter dienstag di dinsdag",
            "wednesday wed mercredi mer mercoledi miercoles mie quarta qua mittwoch mi woensdag wo",
            "thursday thu thur thurs jeudi jeu giovedi gio jueves jue quinta qui donnerstag do donderdag",
            "friday fri vendredi ven venerdi viernes vie sexta sex freitag fr vrijdag vr",
            "saturday sat samedi sam sabato sab sabado samstag sa zaterdag za",
            "sunday sun dimanche dim domenica dom domingo sonntag so zondag zo",
        )
    )
    for word in words.split()
}
# Month names, accents removed, in the same languages; a month is written out or
# cut to its first three to five letters ("Sept.", "janv", "ott").
_MONTH_NAMES = (
    "january janvier gennaio enero janeiro januar januari",
    "february fevrier febbraio febrero fevereiro februar februari",
    "march mars marzo marco marz maart",
    "april avril aprile abril",
    "may mai maggio mayo maio mei",
    "june juin giugno junio junho juni",
    "july juillet luglio julio julho juli",
    "august aout agosto augustus",
    "september septembre settembre septiembre setiembre setembro",
    "october octobre ottobre octubre outubro oktober",
    "november novembre noviembre novembro",
    "december decembre dicembre diciembre dezembro dezember",
)


def _month_words() -> dict[str, int]:
    """Each month's names and short forms; a short form two months share ("jui",
    juin or juillet) names neither."""
    found: dict[str, set[int]] = {}
    for n, names in enumerate(_MONTH_NAMES, start=1):
        for name in names.split():
            for k in (3, 4, 5, len(name)):
                found.setdefault(name[:k], set()).add(n)
    return {word: months.pop() for word, months in found.items() if len(months) == 1}


_MONTHS = _month_words()
_NUMERIC = r"(?P<a>\d{1,4})[./-](?P<b>\d{1,2})(?:[./-](?P<c>\d{2,4}))?\.?"
_NAMED = (
    r"(?P<d1>\d{1,2})(?:st|nd|rd|th|er|º|°)?\.?\s+(?P<m1>[^\W\d_]{3,10})\.?(?:,?\s+(?P<y1>\d{4}))?"
    r"|(?P<m2>[^\W\d_]{3,10})\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<y2>\d{4}))?"
)
_AFTER_DATE = (
    rf"(?:{_NUMERIC}|{_NAMED})(?![\d/.-]\d)"
    r"(?:\s*[-–]\s*\d{1,2}(?:[./]\d{1,2})?(?:[./]\d{2,4})?)?"  # "15-19 Sept", "15/09 – 19/09"
    r"\s*[,:·|–-]?\s*(?:\d{1,2}[:h.]\d{2}(?:\s*[-–]\s*\d{1,2}[:h.]\d{2})?\s*)?"  # a time
    r"(?:(?P<n>\d{1,2})\s+(?=\D))?(?P<rest>.*)$"
)
_DATED = re.compile(
    r"^(?:(?P<w0>\d{1,2})\s+)?(?:(?P<wd>[^\W\d_]{2,12})\.?(?:-feira)?,?\s+)?" + _AFTER_DATE, re.IGNORECASE
)
_DATED_ALONE = re.compile(r"^(?:(?P<w0>\d{1,2})\s+)?(?P<wd>)" + _AFTER_DATE, re.IGNORECASE)
_NUMBERED = re.compile(r"^(?P<n>\d{1,2})[.)]?\s+(?P<rest>\D.*)$")
# The words of a schedule's header row, accents removed: a week column makes its
# numbered rows weeks.
_WEEK_COLUMNS = frozenset({"week", "weeks", "wk", "semaine", "settimana", "semana", "woche", "nr", "n°", "#"})
_HEADER_WORDS = (
    _COLUMNS
    | _WEEK_COLUMNS
    | {
        "materials",
        "material",
        "materiel",
        "materiale",
        "materiali",
        "materiales",
        "lecture",
        "notes",
        "chapter",
        "chapters",
        "chapitre",
        "capitolo",
        "kapitel",
        "homework",
        "assignment",
        "assignments",
        "due",
        "slides",
        "subject",
        "subjects",
        "contents",
        "contenu",
        "contenuti",
        "contenido",
        "inhalt",
        "inhoud",
        "time",
        "heure",
        "ora",
        "hora",
        "zeit",
        "references",
        "and",
        "/",
        "&",
    }
)
# What a row says to read, where it begins: an author before a chapter ("Gallian,
# Part 1, Chapter 3"), a chapter, pages, a section, in the languages read.
_MATERIAL = re.compile(
    r"(?:\b[A-Z][\w'’.-]+(?:\s+(?:et al\.|&|and)\s+[A-Z][\w'’-]+)?,\s*)?"
    r"(?:(?i:part|partie|parte|teil|deel)\s+\w+,\s*)?"
    r"\b(?i:chapters?|ch\.|chap\.|chapitres?|capitoli|capitolo|cap\.|cap[ií]tulos?|kapitel|kap\.|"
    r"hoofdstuk(?:ken)?|sections?|pp?\.|pages?|pagine|p[aá]ginas?|seiten?)\s*\d"
)
_CONTINUED = re.compile(
    r"^(?:continuation|continued|cont\.|suite|seguito|continuazione|continuacion|continuacao|"
    r"fortsetzung|vervolg)\b[\s.:,;–-]*",
    re.IGNORECASE,
)
# A day without class, or a note about one: not a topic.
_OFF = re.compile(
    r"\b(?:no class(?:es)?|no lectures?|holidays?|bank holiday|reading week|"
    r"(?:spring|winter|fall|autumn) break|make-?up|"
    r"pas de cours|vacances|ferie|rattrapage|vacanze|festivo|recupero|lezione sospesa|"
    r"no hay clase|feriado|recuperacion|sem aula|kein unterricht|ferien|vorlesungsfrei|"
    r"vakantie|geen college|inhaal)\b",
    re.IGNORECASE,
)
# An assessment's name, the whole of a row's first sentence: "Mock Exam # 1",
# "Midterm", "Partiel", "Esonero 2", "Presentation session one", accents removed.
_ASSESSMENT = re.compile(
    r"^(?:(?:mock|final|partial|practice|written|oral|in-class|in class|second|first|class)\s+)?"
    r"(?:exam|examen|esame|test|quiz|klausur|tentamen|toets|prova|prueba|midterm|mid-term|partiel|"
    r"parziale|esonero|compitino|parcial|zwischenprufung|deeltoets|interrogation|controle|devoir|"
    r"presentation|soutenance|expose|presentazione|presentacion|referat)(?:s|es|zes|en|i)?"
    r"(?:\s+(?:blanc|partiel|final|finale|scritto|orale|intermedio|intermedia|session|sessions|day))?"
    r"(?:\s*(?:n[o°]\.?|#)?\s*(?:\d{1,2}|one|two|three|four|i{1,3}|iv))?"
    r"(?:\s*\(\s*[^)]*\))?$"
)


def _kind(title: str) -> str:
    """ "assessment" when the title, or its first sentence, names a mock exam, a
    midterm, a quiz or a presentation, "topic" otherwise."""
    first = re.split(r"[.:;,]\s|[.:;,]$", _folded(title), maxsplit=1)[0].strip()
    return "assessment" if _ASSESSMENT.match(first) else "topic"


def _ymd(a: int, b: int, year: int | None, day_first: bool) -> tuple[int, int, int | None] | None:
    d, m = (a, b) if day_first else (b, a)
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    if year is not None:
        try:
            date(year, m, d)
        except ValueError:
            return None
    return d, m, year


def _parse(match: re.Match[str], day_first: bool) -> tuple[int, int, int | None] | None:
    """The day, month and year (or None) a date match names."""
    if match["a"] is not None:
        a, b = int(match["a"]), int(match["b"])
        c = match["c"]
        if len(match["a"]) == 4:  # ISO: year, month, day
            return _ymd(int(match["c"] or 0), b, a, True) if c else None
        year = None if c is None else int(c) + (2000 if len(c) == 2 else 0)
        if c is not None and len(c) == 3:
            return None
        if a > 12 or b > 12:
            day_first = a > 12
        return _ymd(a, b, year, day_first)
    month = _MONTHS.get(_folded(match["m1"] or match["m2"]))
    if month is None:
        return None
    y = match["y1"] or match["y2"]
    return _ymd(int(match["d1"] or match["d2"]), month, int(y) if y else None, True)


def _day_iso(found: tuple[int, int, int | None]) -> str:
    d, m, y = found
    return f"{y:04d}-{m:02d}-{d:02d}" if y else f"{m:02d}-{d:02d}"


def _weekday_ok(match: re.Match[str], found: tuple[int, int, int | None]) -> bool:
    wd = _WEEKDAYS.get(_folded(match["wd"] or "").rstrip(".")) if match["wd"] else None
    d, m, y = found
    return wd is None or y is None or date(y, m, d).weekday() == wd


def _dated(line: str, day_first: bool = True) -> tuple[re.Match[str], tuple[int, int, int | None]] | None:
    """The date a schedule's row starts with, and its match; None when the line does
    not start with one. A number alone ("12.3 Groups") is a section, not a date: a
    date without a month's name needs a year or a weekday."""
    match = _DATED.match(line)
    if match is not None and match["wd"] and _folded(match["wd"]).rstrip(".") not in _WEEKDAYS:
        match = _DATED_ALONE.match(line)  # "Sept 14 Kinematics": a month, not a weekday
    if match is None:
        return None
    found = _parse(match, day_first)
    if found is None:
        return None
    if match["a"] is not None and found[2] is None and not match["wd"]:
        return None
    if not _weekday_ok(match, found):
        flipped = _parse(match, not day_first) if match["a"] is not None else None
        if flipped is None or not _weekday_ok(match, flipped):
            return None
        found = flipped
    return match, found


def _date(line: str) -> tuple[str, int] | None:
    """A list line's leading date, as `Topic.day` keeps it, and where the rest
    begins; or None."""
    dated = _dated(line)
    if dated is None or dated[0]["w0"] is not None or dated[0]["n"] is not None:
        return None
    match, found = dated
    return _day_iso(found), match.start("rest")


def _order(lines: Sequence[str]) -> bool:
    """Whether the schedule writes the day before the month: a number above 12 says
    which comes first, else the weekdays given, else day first."""
    votes = 0
    for line in lines:
        match = _DATED.match(line)
        if match is None or match["a"] is None or len(match["a"]) == 4:
            continue
        a, b = int(match["a"]), int(match["b"])
        if a > 12 >= b:
            return True
        if b > 12 >= a:
            return False
        dated = _dated(line, True)
        if dated is not None and match["wd"] and dated[1][2] is not None:
            votes += 1 if (dated[1][1], dated[1][0]) == (b, a) else -1
    return votes >= 0


def _rows(lines: Sequence[str]) -> list[Topic] | None:
    """A schedule's topics, one or two per row, or None when the text has no
    schedule: fewer than two dated rows, and fewer than three numbered rows under a
    header naming a week column."""
    day_first = _order(lines)
    weeks_column = False
    rows: list[tuple[int | None, str | None, list[str]]] = []
    started = False
    for line in lines:
        cells = _folded(line).replace("·", " ").split()
        if not rows and cells and all(c in _HEADER_WORDS for c in cells) and len(cells) <= 6:
            weeks_column = weeks_column or any(c in _WEEK_COLUMNS for c in cells)
            continue
        dated = _dated(line, day_first)
        if dated is not None:
            match, found = dated
            week = match["w0"] or match["n"]
            rows.append((int(week) if week else None, _day_iso(found), [match["rest"]]))
            started = True
            continue
        numbered = _NUMBERED.match(line) if weeks_column else None
        if numbered is not None and 1 <= int(numbered["n"]) <= 30:
            rows.append((int(numbered["n"]), None, [numbered["rest"]]))
            started = True
            continue
        if not line:
            started = False if rows and len(rows[-1][2]) > 1 else started
            continue
        if rows and started and len(rows[-1][2]) < 8 and not _OFF.match(line):
            rows[-1][2].append(line)
    dated_rows = sum(1 for _, day, _ in rows if day)
    if dated_rows < 2 and sum(1 for _, day, _ in rows if not day) < 3:
        return None
    # A number after the date is the week's when most rows have one, in order.
    numbers = [w for w, day, _ in rows if day and w is not None]
    weeks_given = weeks_column or (
        len(numbers) >= 0.6 * dated_rows and numbers == sorted(numbers) and max(numbers, default=0) <= 30
    )
    topics: list[Topic] = []
    for week, day, parts in rows:
        text = " ".join(parts)
        if day and week is not None and not weeks_given:
            text, week = f"{week} {text}", None
        topics += _row_topics(text, week, day)
    return topics


def _row_topics(text: str, week: int | None, day: str | None) -> list[Topic]:
    """A row's topic, and its assessment ahead of it when the row starts with one
    ("Midterm. Regular expressions"); none for "Continuation" or a day off."""
    text = re.sub(r"\[[^\]]*\]", lambda m: "" if _OFF.search(m[0]) else m[0], text)
    material = None
    cut = _MATERIAL.search(text)
    if cut is not None and cut.start() > 0:
        text, material = text[: cut.start()], _title(text[cut.start() :]).rstrip(" .") or None
    text = _CONTINUED.sub("", " ".join(text.split()))
    title = _title(text)
    if sum(c.isalpha() for c in title) < 3 or (_OFF.search(title) and len(title.split()) <= 5):
        return []
    out = []
    sentence = re.match(r"^(.*?)[.:;]\s+(.+)$", title)
    if sentence is not None and _kind(sentence[1]) == "assessment":
        out.append(Topic(_title(sentence[1]), week, None, day, None, "assessment"))
        title = _title(sentence[2])
    out.append(Topic(title, week, None, day, material, _kind(title)))
    return out


def place(
    topics: Sequence[Topic],
    week_hours: Sequence[float],
    lecture_weeks: Sequence[int],
    mondays: Sequence[date] = (),
) -> list[int | None]:
    """The teaching week (from 1) of each topic, or None when it has none.

    `week_hours` are the lecture hours of each of the course's teaching weeks in
    the timetable, in order; `lecture_weeks` the teaching week of each of its
    lectures, in order; `mondays` the Monday of each. A topic's day places it in the
    teaching week of that day (D90); else its own week or lecture; when no topic
    names one, the list is spread over the weeks in order, each week taking a share
    of the topics as large as its share of the hours. A week or lecture beyond the
    timetable places nothing."""
    if any(t.week is not None or t.lecture is not None or t.day for t in topics):
        # A schedule's days place its topics when one of them falls in a teaching week
        # (a schedule from another year falls back on its week numbers): a day between
        # two teaching weeks goes to the one before, a day outside them to none.
        by_day = any(_on(t.day, mondays) is not None for t in topics if t.day)
        out: list[int | None] = []
        for t in topics:
            if t.day and by_day:
                out.append(_before(t.day, mondays))
            elif t.week is not None:
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


def _before(day: str, mondays: Sequence[date]) -> int | None:
    """The teaching week (from 1) a day falls in, else the last one begun before it
    (a mock exam on a day without lectures); None before the first or after the last."""
    on = _on(day, mondays)
    if on is not None or not mondays:
        return on
    parts = [int(x) for x in day.split("-")]
    years = (parts[0],) if len(parts) == 3 else (mondays[0].year, mondays[-1].year)
    for year in dict.fromkeys(years):
        try:
            when = date(year, parts[-2], parts[-1])
        except ValueError:
            continue
        if mondays[0] <= when <= mondays[-1] + timedelta(days=6):
            return max(k for k, monday in enumerate(mondays, start=1) if monday <= when)
    return None


def _on(day: str, mondays: Sequence[date]) -> int | None:
    """The teaching week (from 1) a schedule's day falls in, or None. A day without
    its year ("09-15") takes the year of the teaching week it falls in."""
    parts = [int(x) for x in day.split("-")]
    for k, monday in enumerate(mondays, start=1):
        years = (parts[0],) if len(parts) == 3 else {monday.year, (monday + timedelta(days=6)).year}
        for year in years:
            try:
                when = date(year, parts[-2], parts[-1])
            except ValueError:
                continue
            if monday <= when <= monday + timedelta(days=6):
                return k
    return None


def to_text(topics: Sequence[Topic], week_word: str = "Week", lecture_word: str = "Lecture") -> str:
    """The topics as lines `read` gives back: "Week 3: ...", "Lecture 5: ...", or
    the title alone. `week_word` and `lecture_word` are the student's language's,
    which `read` knows."""
    lines = []
    for t in topics:
        if t.week is not None:
            lines.append(f"{week_word} {t.week}: {t.title}")
        elif t.day and len(t.day) == 10:
            lines.append(f"{t.day}: {t.title}")
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
        "course description",
        "description",
        "description du cours",
        "descriptif",
        "descrizione",
        "descrizione del corso",
        "descripcion",
        "descripcion del curso",
        "kursbeschreibung",
        "beschreibung",
        "beschrijving",
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
        "learning objectives",
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
        "readings",
        "recommended reading",
        "recommended readings",
        "required reading",
        "required readings",
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
# Headings that end a section without being one of the ECTS guide's items (D90): the
# language, the format, the level, the semester, the credits. A course's staff and
# its hours are found by the words they start with or hold, being written so many ways
# ("Course Coordinator Affiliation", "Total number of hours for the current semester").
_STOPS = {
    heading: name
    for name, headings in {
        "language": (
            "language",
            "language of instruction",
            "teaching language",
            "langue",
            "langue d'enseignement",
            "lingua",
            "lingua di insegnamento",
            "lingua di erogazione",
            "idioma",
            "lengua",
            "unterrichtssprache",
            "sprache",
            "voertaal",
        ),
        "format": ("course format", "format", "teaching format", "format du cours", "formato", "lehrform"),
        "level": ("level", "niveau", "livello", "nivel", "year", "annee", "anno"),
        "semester": ("semester", "semestre", "period", "periode", "periodo", "term", "trimestre"),
        "credits": (
            "credits",
            "number of credits",
            "credit points",
            "ects",
            "ects credits",
            "credits ects",
            "crediti",
            "cfu",
            "creditos",
            "leistungspunkte",
            "studiepunten",
        ),
    }.items()
    for heading in headings
}
_STAFF = re.compile(
    r"^(?:course coordinators?|coordinators?|other instructors?|instructors?|lecturers?|teachers?|"
    r"teaching staff|professors?|enseignants?|responsables?|intervenants?|docenti|docente|titolare|"
    r"profesor(?:es)?|dozent(?:in|en)?|lehrende|docent(?:en)?|e-?mail|webpage|website|"
    r"office hours|heures de permanence|ricevimento|sprechstunden?|spreekuur)\b"
)
_HOURS = re.compile(
    r"\b(?:hours|heures|ore|horas|stunden|uren|contacturen|volume horaire|carga horaria|monte ore|"
    r"prasenzzeit|kontaktzeit|contact time)\b"
)
_LONGEST = max(len(h.split()) for h in [*_SECTION_OF, *_STOPS])
CREDIT_HOURS = (25, 30)  # one ECTS credit is 25 to 30 hours of work [ref:ects2015]
# On one line: "45" above "Credits: 4" is not 45 credits.
_CREDITS = re.compile(
    r"(?:(?<![\d.,])(?P<n>\d{1,2}(?:[.,]5)?)[ \t]*"
    r"(?:ects|cfu|crediti|credits?|credits ects|creditos|creditos ects|lp|leistungspunkte|ec|studiepunten)\b)"
    r"|(?:\b(?:ects|cfu|credits?|crediti|creditos|leistungspunkte|studiepunten)\b[ \t]*[:=]?[ \t]*"
    r"(?P<m>\d{1,2}(?:[.,]5)?)(?![\d.,]\d))",
)
# "and" between two parts, in the languages read: dropped from the front of a label.
# Matched only there, never split on: French "en ligne" is part of "examen en ligne".
_AND = re.compile(r"^(?:and|et|e|y|und|en|plus|ed)\s+", re.IGNORECASE)
_NOT_HEADING = re.compile(r"^\s*[-*•▪◦●■□➢►–—·+]")  # a bulleted line is an item, not a heading


def _heading(line: str) -> tuple[str, str] | None:
    """The section a line opens, and what follows the heading on the same line
    ("Assessment: written exam 70 %"), or None for an ordinary line. Besides the
    ECTS guide's items, a heading that only ends a section (D90): "language",
    "format", "level", "semester", "credits", "staff", "hours"."""
    if _NOT_HEADING.match(line):
        return None
    folded = _folded(_BULLET.sub("", line.strip(), count=1)).strip()
    folded = re.sub(r"^\d{1,2}(?:\.\d{1,2})*\.?\s+", "", folded)  # "3. Course content"
    head, colon, rest = folded.partition(":")
    head = " ".join(head.strip(" .-–—").split())
    words = head.split()
    if not words:
        return None
    name = _SECTION_OF.get(head) or _STOPS.get(head) if len(words) <= _LONGEST else None
    if name is None and colon and len(words) <= 10:
        name = "staff" if _STAFF.match(head) else "hours" if _HOURS.search(head) else None
    if name is None:
        return None
    if not colon and rest:
        return None
    start = len(line) - len(rest.lstrip()) if colon and rest.strip() else len(line)
    return name, line[start:].strip()


def _cut(text: str) -> list[tuple[str, str]]:
    """A course page cut at its headings: each heading's kind and its text, in order."""
    found: list[tuple[str, list[str]]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        heading = _heading(line)
        if heading is not None:
            name, rest = heading
            found.append((name, [rest] if rest else []))
        elif found:
            found[-1][1].append(line)
    return [(name, "\n".join(lines).strip()) for name, lines in found]


def sections(text: str) -> dict[str, str]:
    """A course page cut at its headings: each section's text, the longest of each
    kind ("Exam: 15 January" above "Assessment methods" and their list keeps the
    list); nothing when the text has none of the headings."""
    out: dict[str, str] = {}
    for name, body in _cut(text):
        if name in SECTIONS and len(body) > len(out.get(name, "")):
            out[name] = body
    return out


def course_text(text: str) -> str:
    """The part of a pasted course page that lists its topics: of its content
    sections (a description and a schedule may both be one), the one with the most
    topics; with headings but no content section, the lines under none of them; all
    of it when it has no heading."""
    cut = _cut(text)
    if not cut:
        return text
    contents = [body for name, body in cut if name == "content"]
    if contents:
        return max(contents, key=lambda body: len(read(body)))
    first = _heading_index(text)
    return "\n".join(text.replace("\r\n", "\n").split("\n")[:first])


def _heading_index(text: str) -> int:
    for i, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        if _heading(line) is not None:
            return i
    return 0


@dataclass(frozen=True)
class Part:
    """A part of the course's assessment: "Written exam", 70 (percent), and the
    teaching weeks it is held in when the text says so ("in weeks 3 and 10", D90)."""

    label: str
    percent: int
    weeks: tuple[int, ...] = ()


_PERCENT = re.compile(r"\(?\s*(?P<p>\d{1,3}(?:[.,]\d)?)\s*%\s*\)?")
_PARENS = re.compile(r"\([^()]*\)")
_WEEKS = re.compile(
    r"\b(?:weeks?|wk|semaines?|settimane|settimana|semanas?|wochen?)\s*(\d{1,2}(?:\s*(?:,|and|et|e|y|und|en|&)\s*\d{1,2})*)",
    re.IGNORECASE,
)


def _clause(text: str, last: bool) -> str:
    """The last (or first) clause of `text`, its asides in brackets dropped:
    "(short quiz…), midterm" gives "midterm"."""
    text = _PARENS.sub(" ", _PARENS.sub(" ", text))
    clauses = [c for c in re.split(r"[,;]\s*|[.:](?:\s+|$)", text) if c.strip()]
    if not clauses:
        return ""
    return _title(_AND.sub("", (clauses[-1] if last else clauses[0]).strip()))


def assessment(text: str) -> list[Part]:
    """The assessment's parts with their percentages, from the assessment section
    (none without one): "Written exam 70 %, project 30 %", "(70 %) written exam",
    "Partial exam 1 (20%)", "2 midterms in weeks 7 and 14: 80% (40% + 40%)": a
    percentage in brackets after a part's own splits it and is not a part. Kept only
    when they add up to 100, within 1, the way a weighting must."""
    found = sections(text).get("assessment", "")
    parts = []
    for line in _logical([x.strip() for x in found.split("\n")]):
        line = _BULLET.sub("", line, count=1)
        pos = 0
        matches = list(_PERCENT.finditer(line))
        for i, m in enumerate(matches):
            before = line[pos : m.start()]
            percent = round(float(m["p"].replace(",", ".")))
            bracket = (m[0].lstrip().startswith("("), m[0].rstrip().endswith(")"))
            if before.count("(") > before.count(")") or bracket in ((True, False), (False, True)):
                pos = m.end()  # "(10% + 10%)", "(of which oral 20 %)": inside another part
                continue
            label, source = _clause(before, last=True), before
            if sum(c.isalpha() for c in label) < 3:
                nxt = matches[i + 1].start() if i + 1 < len(matches) else len(line)
                after = line[m.end() : nxt]
                label, source = _clause(after, last=False), after
                if sum(c.isalpha() for c in label) < 3:
                    pos = m.end()  # "(10% + 10%)": the split of the part before
                    continue
                pos = m.end() + len(after.split(",")[0].split(";")[0])
            else:
                pos = m.end()
            if 0 < percent <= 100:
                weeks = _WEEKS.search(source)
                found_weeks = tuple(int(n) for n in re.findall(r"\d{1,2}", weeks[1])) if weeks else ()
                parts.append(Part(label[0].upper() + label[1:], percent, found_weeks))
    return parts if parts and abs(sum(p.percent for p in parts) - 100) <= 1 else []


_HOUR_VALUES = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,]5)?)\s*(?:h\b|hrs?\b|hours?\b|heures?\b|ore\b|horas?\b|stunden\b|std\b|"
    r"uur\b|uren\b)",
    re.IGNORECASE,
)
_PER_WEEK = re.compile(
    r"per week|a week|/\s*week|par semaine|/\s*semaine|a settimana|alla settimana|/\s*settimana|"
    r"por semana|/\s*semana|pro woche|/\s*woche|wochentlich|per week",
    re.IGNORECASE,
)


def hours(text: str) -> float | None:
    """The course's hours of classes in the semester (lectures, tutorials, labs),
    from the line that says them: "84h / semester", "42h Lectures + 42h Tutorials",
    "21 hours CM / 21 hours TP" (D90). None for hours a week, or none given."""
    for name, body in _cut(text):
        if name != "hours":
            continue
        line = next((x for x in body.split("\n") if x.strip()), "")
        if _PER_WEEK.search(_folded(line)):
            return None
        values = [float(v.replace(",", ".")) for v in _HOUR_VALUES.findall(line)]
        if not values:
            bare = re.match(r"^\s*(\d{1,3})\b(?!\s*%)", line)
            values = [float(bare[1])] if bare else []
        total = sum(values)
        if 1 <= total <= 600:
            return total
    return None


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


# --------------------------------------------------------------------------- #
# A whole semester's program: its courses (D90)
# --------------------------------------------------------------------------- #

# A program document (a semester's syllabus booklet, often a PDF) holds every course
# of the semester, one after another, under page headers and footers. Each course
# starts at its title: a name from the contents page, a name of the student's
# timetable, or, without either, the title above each repetition of the headings a
# course has (its outcomes, content, assessment…).

_LEADER = re.compile(r"^(?P<name>.*?[^\s.…·_])\s*(?:[.…·_]\s*){4,}(?P<page>\d{1,3})\s*$")
_PAGE = re.compile(
    r"^(?:[-–—]\s*)?(?:(?:page|p\.|pag\.|pagina|seite|bladzijde)\s*)?\d{1,3}"
    r"(?:\s*(?:/|of|sur|di|de|von|van)\s*\d{1,3})?(?:\s*[-–—])?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Course:
    """One course of a program document: its name as the document writes it (empty
    when none was found) and its text, the title left out."""

    name: str
    text: str


def _key(name: str) -> str:
    return " ".join(_folded(name).replace("’", "'").split()).strip(" .:;,-–—")


def clean(text: str) -> tuple[list[str], list[str]]:
    """A program document's lines without what every page repeats (a header, a
    footer, page numbers), and the course names its contents page lists. A line
    repeated three times or more is furniture unless it is a heading, a week, a
    schedule's row, an item of a list or a field ("Level: Year 2")."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    counts: dict[str, int] = {}
    for line in lines:
        if line.strip():
            counts[line.strip()] = counts.get(line.strip(), 0) + 1
    names: list[str] = []
    out: list[str] = []
    for line in lines:
        bare = line.strip()
        leader = _LEADER.match(bare)
        if leader is not None:
            names.append(" ".join(leader["name"].split()))
            continue
        if _PAGE.match(bare):
            out.append("")
            continue
        if (
            counts.get(bare, 0) >= 3
            and len(bare) <= 80
            and ":" not in bare
            and not _BULLET.match(bare)
            and _heading(bare) is None
            and _marker(bare) is None
            and _dated(bare) is None
        ):
            out.append("")
            continue
        out.append(bare)
    return out, list(dict.fromkeys(names))


def _field(line: str) -> bool:
    """A "Label: value" line above a course's first heading ("Course Coordinator:
    …"), or a heading that only ends a section."""
    heading = _heading(line)
    if heading is not None:
        return heading[0] not in SECTIONS
    head, colon, _ = line.partition(":")
    return bool(colon) and 0 < len(head.split()) <= 5


def _titled(line: str) -> bool:
    """A line that could be a course's title: short, no colon, not an item, not the
    end of a sentence."""
    return (
        0 < len(line) <= 80
        and ":" not in line
        and not _BULLET.match(line)
        and not line.endswith((".", ","))
        and sum(c.isalpha() for c in line) >= 3
        and _heading(line) is None
    )


def _title_above(lines: Sequence[str], at: int) -> tuple[int, str]:
    """Where a course begins that has its first heading at line `at`, and its name:
    above the fields, the last of the title lines (the document's own title may be
    above it), unless it only repeats words of one above ("Algebra 3", then
    "Algebra", gives "Algebra 3"); a line that starts in lower case goes on the one
    above."""
    j = at - 1
    while j >= 0 and (not lines[j] or _field(lines[j])):
        j -= 1
    run: list[int] = []
    while j >= 0 and _titled(lines[j]):
        run.insert(0, j)
        j -= 1
    if not run:
        return at, ""
    pick = run[-1]
    words = set(_folded(lines[pick]).split())
    for k in run[:-1]:
        if words <= set(_folded(lines[k]).split()):
            pick = k
            break
    if lines[pick][:1].islower() and pick - 1 in run:
        return pick - 1, f"{lines[pick - 1]} {lines[pick]}"
    return pick, lines[pick]


def _by_headings(lines: Sequence[str]) -> list[tuple[int, str]]:
    """Courses found by their headings alone, each named by the title above it: of
    the kinds of heading that repeat, the one with a title above the most of its
    headings opens each course, when each stretch it opens holds two kinds or more."""
    kinds = [(i, h[0]) for i, line in enumerate(lines) if line and (h := _heading(line)) is not None]
    best: list[tuple[int, str]] = []
    for lead in dict.fromkeys(kind for _, kind in kinds if kind in SECTIONS):
        opens = [i for i, kind in kinds if kind == lead]
        if len(opens) < 2:
            continue
        stretches = [
            {kind for i, kind in kinds if a <= i < b}
            for a, b in zip(opens, [*opens[1:], len(lines)], strict=True)
        ]
        if any(len(kinds_in) < 2 for kinds_in in stretches):
            continue
        titled = [(at, name) for at, name in (_title_above(lines, i) for i in opens) if name]
        if len(titled) > len(best):
            best = titled
    return best if len(best) >= 2 else []


def courses(text: str, names: Sequence[str] = ()) -> list[Course]:
    """The courses of a program document, in order: each begins at a line that is a
    name from its contents page or from `names` (the student's courses), else at
    the title above each repetition of its headings. A document with one course, or
    none found, gives it whole, without a name."""
    lines, listed = clean(text)
    known = {_key(n): n for n in [*listed, *names] if _key(n)}
    starts: list[tuple[int, str]] = []
    taken: set[str] = set()
    for i, line in enumerate(lines):
        key = _key(line)
        if key in known and key not in taken:
            taken.add(key)
            starts.append((i, line))
    for i, name in _by_headings(lines):
        starts.append((i, name))
    starts.sort()
    kept: list[tuple[int, str]] = []
    for i, name in starts:
        if kept and i - kept[-1][0] <= 3:
            continue  # the same title found twice, or a subtitle under it
        kept.append((i, name))
    if len(kept) < 2:
        return [Course(kept[0][1] if kept else "", "\n".join(lines).strip())]
    ends = [i for i, _ in kept[1:]] + [len(lines)]
    return [
        Course(" ".join(name.split()), "\n".join(lines[i + 1 : end]).strip())
        for (i, name), end in zip(kept, ends, strict=True)
    ]
