"""
A course's study plan from its own materials on Moodle (DECISIONS.md D208).

The owner, 2026-11-26: "the most important step: connect the materials from Moodle and
parse them, and so create a structured plan of what to study, in what progression and
how, even if it requires API calls as long as they don't cost too much." Then: the
student gives the course's Moodle address as before (D163), the app reads the course with
the student's own Moodle key, read-only, keeps the text and the plan, not the files.

This module is pure: what Moodle's `core_course_get_contents` answered, read into the
course's sections and their files; which files are read, within limits; and the plan
built from the files' text by rules, when the model is not used. The model's plan is
asked for and checked in `ai.py`; the network is `sources.py`'s; the plan's place in the
student's plan is `service.py`'s.

What the plan is, week by week: the topics in the order to learn them, each with what the
materials say are its key points; what must be known first; and how to study it, by the
methods the app's tips rest on (testing oneself, worked examples before problems, exam
practice). The rules know the files' names and headings only, so their plan says less:
the topics are the files' and sections' titles, and the "how" follows from the kind of
file (slides, exercises, solutions, a past exam).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from .coursepages import folded

# What is downloaded and read: notes' kinds (`notefiles`), within the limits below. A
# course of twelve weeks of slides is usually 20 to 60 files; a video or an archive is
# never fetched.
READABLE = (".pdf", ".pptx", ".docx", ".odt", ".odp", ".md", ".txt")
FILE_MOST = 10 * 1024 * 1024  # bytes of one file: `notefiles.MAX_BYTES`
FILES_MOST = 60  # files read from one course
COURSE_BYTES = 120 * 1024 * 1024  # bytes downloaded for one course, all files
TEXT_PER_FILE = 12_000  # characters kept from one file: its first pages say what it is about
TEXT_MOST = 160_000  # characters of a course sent to the model: under 100,000 tokens, its price step
# (`ai.LONG_PROMPTS`), even at two characters a token (`benchmarks/syllabus_cost.py`)
TOPICS_A_WEEK = 6
POINTS_A_TOPIC = 4
HOW_A_WEEK = 4
HOW_KINDS = ("read", "self-test", "worked-example", "exercises", "summary", "exam-practice")


@dataclass(frozen=True)
class File:
    """One file of a course on Moodle: the activity it belongs to (its name, its page on
    Moodle), the file's own name, its address for the web service, its size."""

    activity: str
    page: str
    filename: str
    address: str
    size: int
    section: str
    number: int

    @property
    def readable(self) -> bool:
        return self.filename.lower().endswith(READABLE) and 0 < self.size <= FILE_MOST


@dataclass(frozen=True)
class Activity:
    """One activity of a section that holds material: its name, kind (`modname`), its page
    on Moodle and its files."""

    name: str
    kind: str
    page: str
    files: tuple[File, ...] = ()


@dataclass(frozen=True)
class Section:
    name: str
    number: int
    activities: tuple[Activity, ...] = ()


_KINDS = ("resource", "folder", "page", "book", "lesson", "imscp", "url", "quiz", "h5pactivity")


def sections(contents: object, site: str) -> list[Section]:
    """Moodle's answer to `core_course_get_contents` read into sections, keeping only the
    activities that hold material and only files on the same site as the course (a file's
    address carries the student's key when it is fetched, so it must never lead
    elsewhere). Anything not shaped as Moodle answers is left out."""
    host = (urlsplit(site).hostname or "").lower()
    out: list[Section] = []
    for raw in contents if isinstance(contents, list) else []:
        if not isinstance(raw, Mapping):
            continue
        name = " ".join(str(raw.get("name") or "").split())
        number = raw.get("section")
        number = number if isinstance(number, int) else len(out)
        activities = []
        for module in raw.get("modules") or []:
            if not isinstance(module, Mapping) or module.get("modname") not in _KINDS:
                continue
            if module.get("visible") == 0 or module.get("uservisible") is False:
                continue
            title = " ".join(str(module.get("name") or "").split())
            page = str(module.get("url") or "")
            if not title or not page.lower().startswith(("https://", "http://")):
                continue
            files = []
            for item in module.get("contents") or []:
                if not isinstance(item, Mapping) or item.get("type") != "file":
                    continue
                address = str(item.get("fileurl") or "")
                if (urlsplit(address).hostname or "").lower() != host or not address.startswith("http"):
                    continue
                size = item.get("filesize")
                files.append(
                    File(
                        title,
                        page,
                        str(item.get("filename") or ""),
                        address,
                        size if isinstance(size, int) else 0,
                        name,
                        number,
                    )
                )
            activities.append(Activity(title, str(module["modname"]), page, tuple(files)))
        out.append(Section(name, number, tuple(activities)))
    return out


def to_read(found: Sequence[Section]) -> tuple[list[File], int]:
    """The files to download and read, in the course's order, within `FILES_MOST` and
    `COURSE_BYTES`; and how many readable files were left out by those limits."""
    chosen: list[File] = []
    total = 0
    left = 0
    for section in found:
        for activity in section.activities:
            for file in activity.files:
                if not file.readable:
                    continue
                if len(chosen) >= FILES_MOST or total + file.size > COURSE_BYTES:
                    left += 1
                    continue
                chosen.append(file)
                total += file.size
    return chosen, left


def with_key(address: str, key: str) -> str:
    """A file's address for the web service: Moodle reads the key from the address, as its
    own app sends it (`webservice/pluginfile.php`)."""
    joiner = "&" if "?" in address else "?"
    return f"{address}{joiner}token={key}"


# -- the rules' plan, when the model is not used --------------------------------------------

_EXERCISES = re.compile(
    r"\b(exercises?|exercices?|esercizi\w*|ejercicios?|problems?|problem sets?|worksheets?|td\d*|tp\d*|"
    r"homework|devoirs?|uebung\w*|übung\w*|oefening\w*|series?|feuilles?)\b"
)
_SOLUTIONS = re.compile(
    r"\b(solutions?|corrig[eé]s?|correction|soluzion[ei]|soluciones?|loesung\w*|lösung\w*|answers?)\b"
)
_EXAM = re.compile(
    r"\b(past (?:paper|exam)s?|exams?|examens?|esam[ei]|ex[aá]menes|annales|partiels?|midterms?|finals?|"
    r"mock|pruefung\w*|prüfung\w*|tentamen)\b"
)
_HEADING = re.compile(r"^#{1,3}\s+(.{3,90})$", re.M)


def kind_of(name: str) -> str:
    """What a file is for, from its name: exercises, solutions, exam practice, or reading
    (slides, notes, a chapter)."""
    words = folded(name)
    if _SOLUTIONS.search(words):
        return "solutions"
    if _EXAM.search(words):
        return "exam"
    if _EXERCISES.search(words):
        return "exercises"
    return "reading"


def headings(text: str, most: int = 3) -> list[str]:
    """The first headings of a file's text, as `notefiles` marks them (slide titles,
    a document's headings), each once."""
    seen: list[str] = []
    for found in _HEADING.finditer(text):
        title = " ".join(found.group(1).split()).strip(" :.-–—")
        if title and folded(title) not in {folded(s) for s in seen}:
            seen.append(title)
        if len(seen) >= most:
            break
    return seen


@dataclass(frozen=True)
class Read:
    """One file read: what it is called, the week it is on (a Monday, or "" for the whole
    course, or "exams"), its kind and its text."""

    title: str
    week: str
    text: str
    kind: str


def rules_plan(
    weeks: Sequence[tuple[str, int]], read: Sequence[Read], *, continued: bool = False
) -> list[dict]:
    """A plan without the model: for each teaching week (its Monday, its number) that has
    files, the topics are the reading's titles and its first headings, and the "how" comes
    from the kinds of files there are. Its words are keys the page translates. With
    `continued` (a course read in its order, D212), a week without files goes on with the
    last week that had some, marked so, as a chapter runs over its weeks (D187)."""
    out: list[dict] = []
    for monday, number in weeks:
        here = [r for r in read if r.week == monday]
        if not here:
            last = next((w for w in reversed(out) if not w.get("continued")), None)
            if continued and last is not None:
                out.append({**last, "monday": monday, "number": number, "continued": last["number"]})
            continue
        topics = []
        for r in here:
            if r.kind != "reading":
                continue
            topics.append({"title": r.title, "points": headings(r.text)[:POINTS_A_TOPIC]})
        kinds = {r.kind for r in here}
        how = []
        if "reading" in kinds:
            how += [{"kind": "read", "what": ""}, {"kind": "self-test", "what": ""}]
        if "exercises" in kinds:
            how.append({"kind": "exercises", "what": ""})
        if "exam" in kinds:
            how.append({"kind": "exam-practice", "what": ""})
        out.append(
            {
                "monday": monday,
                "number": number,
                "topics": topics[:TOPICS_A_WEEK]
                or [{"title": r.title, "points": []} for r in here][:TOPICS_A_WEEK],
                "needs": [],
                "how": how[:HOW_A_WEEK],
            }
        )
    return out


def clip(text: str, most: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[: most - 1].rstrip() + "…"


def checked_plan(data: object, weeks: Sequence[tuple[str, int]]) -> list[dict] | None:
    """The model's plan, kept to its shape and to the course's weeks: each week once and in
    order, at most `TOPICS_A_WEEK` topics of at most `POINTS_A_TOPIC` points, at most
    `HOW_A_WEEK` ways to study of the known kinds. None when nothing usable came back."""
    if not isinstance(data, Mapping) or not isinstance(data.get("weeks"), list):
        return None
    by_number = {number: monday for monday, number in weeks}
    out: dict[int, dict] = {}
    for raw in data["weeks"]:
        if not isinstance(raw, Mapping):
            continue
        number = raw.get("week")
        if not isinstance(number, int) or number not in by_number or number in out:
            continue
        topics = []
        for t in raw.get("topics") or []:
            if not isinstance(t, Mapping) or not str(t.get("title") or "").strip():
                continue
            points = [clip(p, 200) for p in (t.get("points") or []) if str(p).strip()][:POINTS_A_TOPIC]
            topics.append({"title": clip(t["title"], 120), "points": points})
        how = []
        for h in raw.get("how") or []:
            if isinstance(h, Mapping) and h.get("kind") in HOW_KINDS and str(h.get("what") or "").strip():
                how.append({"kind": h["kind"], "what": clip(h["what"], 240)})
        needs = [clip(n, 120) for n in (raw.get("needs") or []) if str(n).strip()][:4]
        if topics:
            out[number] = {
                "monday": by_number[number],
                "number": number,
                "topics": topics[:TOPICS_A_WEEK],
                "needs": needs,
                "how": how[:HOW_A_WEEK],
            }
    return [out[n] for n in sorted(out)] or None


# -- D211: the student's courses on Moodle, matched to the plan's by their names ------------------
# The owner, after D209: "it takes time to copy and paste everything ... all the links from all
# courses". With the key, Moodle lists the courses the student is enrolled in
# (`core_enrol_get_users_courses`); each plan course is matched to the one whose name holds all its
# words, a code, a year or a semester around them being left over (ADE and Moodle are fed from the
# same catalogue, so the words are the same; a Roman numeral reads as its number).

_SMALL_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "de",
        "des",
        "du",
        "la",
        "le",
        "les",
        "l",
        "d",
        "et",
        "en",
        "un",
        "une",
        "au",
        "aux",
        "di",
        "del",
        "della",
        "dei",
        "e",
        "il",
        "lo",
        "gli",
        "y",
        "el",
        "los",
        "las",
        "por",
    ]
)
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8"}


def name_words(name: str) -> frozenset[str]:
    """A course's name as the set of its words that tell it apart: folded, Roman numerals as
    numbers, small words dropped, but a capital letter alone kept, the label of a group
    ("Statistics A" is not "Statistics B")."""
    out = set()
    for raw in re.findall(r"[^\W_]+", name):
        label = len(raw) == 1 and raw.isupper()
        for word in re.findall(r"[a-z0-9]+", folded(raw)):
            if word not in _SMALL_WORDS or label:
                out.add(_ROMAN.get(word, word))
    return frozenset(out)


def match_courses(plan: Sequence[str], moodle: Sequence[tuple[int, str]]) -> dict[str, int]:
    """Each plan course to the Moodle course (its number, its names) whose name holds all the
    plan course's words, the closest when several do (fewest words left over); a Moodle course
    goes to one plan course at most; a plan course with none, or with two as close, is left for
    the student to choose."""
    wanted = {name: name_words(name) for name in plan}
    offered = [(number, name_words(names)) for number, names in moodle]
    out: dict[str, int] = {}
    taken: set[int] = set()
    # The most specific plan courses first, so "Algebra 3" is not taken by "Algebra".
    for name in sorted(plan, key=lambda n: -len(wanted[n])):
        words = wanted[name]
        if not words:
            continue
        close = sorted((len(have - words), number) for number, have in offered if words <= have)
        close = [(extra, number) for extra, number in close if number not in taken]
        if not close or (len(close) > 1 and close[0][0] == close[1][0]):
            continue
        out[name] = close[0][1]
        taken.add(close[0][1])
    return out
