"""
Calendar ingestion and export.

This is stage 1 of the architecture in the November 2025 proposal — "parse user
input" — which the January submission declared and never implemented. Appendix
A.3 contained:

    def parse_user_calendar(file_path, days=60):
        \"\"\"Parses a user's .ics calendar file.\"\"\"
        # ... (File reading logic with icalendar library) ...
        return grid, subjects

with the import commented out and the body a comment. The scheduler therefore
only ever ran on `apply_mock_schedule()`, so nothing in the reported results was
ever tested against a real calendar.

Why .ics rather than a Google Calendar integration
--------------------------------------------------
RFC 5545 is the lowest common denominator that every calendar already speaks:
Google Calendar, Apple Calendar, Outlook and Notion Calendar all export and
import it. That means no OAuth, no API keys, no client verification, and a demo
that runs on a stranger's machine with their real timetable. A Google Calendar
adapter is a later convenience on top of this, not a prerequisite.

Four decisions that matter, all of which are easy to get silently wrong
----------------------------------------------------------------------
1. **Busy intervals round outward.** A lecture from 18:10 to 18:50 blocks both
   the 18:00 and 18:30 slots. Rounding to nearest would leave half a lecture
   marked free, and the scheduler would cheerfully book study time inside it.
   The asymmetry is deliberate: over-blocking costs a study slot, under-blocking
   produces a plan the student cannot follow.

2. **RRULE expansion is mandatory, not optional.** A student's calendar is mostly
   recurring lectures. A parser that reads DTSTART and ignores RRULE sees one
   lecture where there are thirteen weeks of them, marks the rest of the term
   free, and produces a schedule that collides with every class after the first.

3. **Slots are indexed by local wall clock.** Which means a day containing a DST
   transition has 23 or 25 real hours mapped onto 48 half-hour slots. The
   alternative — indexing by elapsed UTC — would shift every lecture by an hour
   after the transition, which is far worse. The residual error is one hour of
   drift in the elapsed-time arithmetic, and FSRS stability is measured in days,
   so it costs about 1/24 of a day on the affected interval. Stated rather than
   hidden; see `test_calendar_io.py`.

4. **A naive UNTIL against an aware DTSTART raises in dateutil.** This is the
   single most common .ics parsing crash, and real exports from real calendars
   contain it. Normalised here instead of propagating.
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from icalendar import Calendar, Event

from .timegrid import SLOTS_PER_DAY, TimeGrid

DEADLINE_KEYWORDS: tuple[str, ...] = (
    "exam",
    "examen",
    "esame",
    "midterm",
    "final",
    "quiz",
    "test",
    "controllo",
    "prova",
    "klausur",
    "contrôle",
    "partiel",
)


@dataclass(frozen=True, slots=True)
class BusyEvent:
    """One occurrence of a calendar event, resolved to a concrete local interval."""

    start: datetime
    end: datetime
    summary: str

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def all_day(self) -> bool:
        return self.start.time() == time(0, 0) and self.duration >= timedelta(days=1)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _as_datetime(value, zone: tzinfo) -> tuple[datetime, bool]:
    """Coerce a DTSTART/DTEND value to an aware datetime in `zone`.

    Returns the datetime and whether the original was a DATE (all-day).
    """
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=zone)
        return aware.astimezone(zone), False
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), tzinfo=zone), True
    raise TypeError(f"unsupported date value {value!r}")


def _event_end(component: Any, start: datetime, was_date: bool, zone: tzinfo) -> datetime:
    if "DTEND" in component:
        end, _ = _as_datetime(component["DTEND"].dt, zone)
        return end
    if "DURATION" in component:
        return start + component["DURATION"].dt
    # RFC 5545: a DATE-valued DTSTART with no DTEND is one whole day.
    return start + (timedelta(days=1) if was_date else timedelta(0))


def _normalise_until(rule_text: str, dtstart: datetime) -> str:
    """Make UNTIL's awareness match DTSTART's.

    dateutil raises `ValueError: RRULE UNTIL values must be specified in UTC when
    DTSTART is timezone-aware`, and plenty of real exports violate that. Rather
    than let the crash reach the caller, coerce.
    """
    if "UNTIL" not in rule_text.upper():
        return rule_text
    parts = []
    for chunk in rule_text.split(";"):
        key, _, value = chunk.partition("=")
        if key.strip().upper() == "UNTIL" and dtstart.tzinfo is not None and not value.endswith("Z"):
            value = value.split("T")[0] + "T235959Z" if "T" not in value else value + "Z"
        parts.append(f"{key}={value}" if value else chunk)
    return ";".join(parts)


def decode_ics(data: bytes) -> str:
    """Bytes of an .ics file to text, tolerating what real files contain.

    RFC 5545 requires UTF-8, and Google's exports comply. But a file opened and
    re-saved in Windows Notepad picks up a byte-order mark, which decodes to
    U+FEFF and makes the parser raise on the very first line; `utf-8-sig` strips
    it. Bytes that are not valid UTF-8 are replaced rather than raised, since a
    single mangled character in a SUMMARY should not make a whole timetable
    unreadable.
    """
    return data.decode("utf-8-sig", errors="replace")


def expand_events(
    ics_text: str,
    window_start: datetime,
    window_end: datetime,
    zone: tzinfo | None = None,
    max_occurrences: int = 5000,
) -> list[BusyEvent]:
    """Every event occurrence overlapping the window, with RRULEs expanded.

    Occurrences are clipped to the window rather than dropped, so a lecture that
    began before the planning horizon still blocks its tail.
    """
    zone = zone or window_start.tzinfo or UTC
    calendar = Calendar.from_ical(ics_text.lstrip("\ufeff"))
    out: list[BusyEvent] = []

    # icalendar types a property value as a union of some thirty classes; which one
    # a property holds is only known at run time, and every access below is guarded
    # by a membership test. So the component is handled as dynamic here, once.
    component: Any
    for component in calendar.walk("VEVENT"):
        if "DTSTART" not in component:
            continue
        # RFC 5545 STATUS:CANCELLED: the lecture is not happening, so its time is
        # free (AUDIT.md item 31).
        if str(component.get("STATUS", "")).strip().upper() == "CANCELLED":
            continue
        # University exports often keep the status and write it in the title
        # instead: "Ethics, Grp: CM ., Salle: 4, COURS ANNULE" (AUDIT.md item 34).
        if _CANCELLED_TITLE.search(str(component.get("SUMMARY", ""))):
            continue
        start, was_date = _as_datetime(component["DTSTART"].dt, zone)
        end = _event_end(component, start, was_date, zone)
        length = max(end - start, timedelta(0))
        summary = str(component.get("SUMMARY", "")).strip()

        excluded: set[datetime] = set()
        if "EXDATE" in component:
            raw = component["EXDATE"]
            for item in raw if isinstance(raw, list) else [raw]:
                for entry in item.dts:
                    excluded.add(_as_datetime(entry.dt, zone)[0])

        if "RRULE" in component:
            rule_text = _normalise_until(component["RRULE"].to_ical().decode("utf-8"), start)
            occurrences = rrulestr(rule_text, dtstart=start).between(
                window_start - length, window_end, inc=True
            )
        else:
            occurrences = [start]

        for occurrence in occurrences[:max_occurrences]:
            if occurrence.tzinfo is None:
                occurrence = occurrence.replace(tzinfo=zone)
            occurrence = occurrence.astimezone(zone)
            if occurrence in excluded:
                continue
            finish = occurrence + length
            if finish <= window_start or occurrence >= window_end:
                continue
            out.append(
                BusyEvent(
                    start=max(occurrence, window_start),
                    end=min(finish, window_end),
                    summary=summary,
                )
            )

    out.sort(key=lambda e: (e.start, e.end))
    return out


# --------------------------------------------------------------------------- #
# Events -> TimeGrid
# --------------------------------------------------------------------------- #


def busy_grid(
    events: Iterable[BusyEvent],
    start_date: date,
    days: int,
    slots_per_day: int = SLOTS_PER_DAY,
) -> TimeGrid:
    """Mark every slot that any event touches as busy.

    Outward rounding: `floor` on the start, `ceil` on the end. See decision 1 in
    the module docstring.
    """
    minutes_per_slot = 24 * 60 // slots_per_day
    grid = TimeGrid(days=days, slots_per_day=slots_per_day)

    for event in events:
        first = _slot_index(event.start, start_date, minutes_per_slot, slots_per_day, math.floor)
        last = _slot_index(event.end, start_date, minutes_per_slot, slots_per_day, math.ceil)
        if last <= first:
            last = first + 1
        grid = grid.block(first, last - first)
    return grid


def _slot_index(
    moment: datetime, start_date: date, minutes_per_slot: int, slots_per_day: int, round_fn
) -> int:
    day_offset = (moment.date() - start_date).days
    minutes = moment.hour * 60 + moment.minute + moment.second / 60
    return day_offset * slots_per_day + int(round_fn(minutes / minutes_per_slot))


def load_availability(
    ics_text: str,
    start_date: date,
    days: int,
    zone_name: str = "UTC",
    slots_per_day: int = SLOTS_PER_DAY,
    study_window: tuple[float, float] | None = (8.0, 22.0),
) -> tuple[TimeGrid, list[BusyEvent]]:
    """The whole ingestion path: .ics text in, occupancy grid out.

    `study_window` is not a detail — it is the difference between a usable
    product and a joke. A calendar records when you are *busy*; nobody puts an
    event on Google Calendar for sleeping. Ingest a real timetable and the first
    free slot of every day is 00:00, so the scheduler dutifully proposes a study
    block at midnight. Measured on the test timetable: the first exported session
    landed at 00:00–01:30.

    So availability is busy-time *plus* stated preferences, and the preferences
    have to come from the user because they are not in the data. Pass None to opt
    out and get raw calendar occupancy.

    Returns the grid and the expanded occurrences, because a user told "no free
    slots on Tuesday" deserves to be shown which events said so.
    """
    zone = ZoneInfo(zone_name)
    window_start = datetime.combine(start_date, time(0, 0), tzinfo=zone)
    window_end = window_start + timedelta(days=days)
    events = expand_events(ics_text, window_start, window_end, zone)
    return availability_from_events(events, start_date, days, slots_per_day, study_window), events


def availability_from_events(
    events: Iterable[BusyEvent],
    start_date: date,
    days: int,
    slots_per_day: int = SLOTS_PER_DAY,
    study_window: tuple[float, float] | None = (8.0, 22.0),
) -> TimeGrid:
    """Occupancy grid from events, whatever produced them, plus the study window.

    The second half of `load_availability`, split out so that events typed into a
    table (`busy_from_table`) go through exactly the same path as an `.ics`.
    """
    grid = busy_grid(events, start_date, days, slots_per_day)
    if study_window is not None:
        earliest, latest = study_window
        if not 0 <= earliest < latest <= 24:
            raise ValueError("study_window must be (earliest, latest) with 0 <= earliest < latest <= 24")
        grid = grid.block_daily(latest, earliest)
    return grid


# --------------------------------------------------------------------------- #
# Busy time typed in by hand
# --------------------------------------------------------------------------- #

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True, slots=True)
class BusyRow:
    """One row of a hand-typed timetable.

    Either weekly (`weekday`, 0 = Monday) or one-off (`on`, a date), never both.
    An `end` at or before `start` runs past midnight into the next day, so
    "23:00 to 07:00" is a night. `end` may be 24:00, written as `time.max`.
    """

    label: str
    start: time
    end: time
    weekday: int | None = None
    on: date | None = None

    def __post_init__(self) -> None:
        if (self.weekday is None) == (self.on is None):
            raise ValueError(f"row {self.label!r}: give either a weekday or a date, not both or neither")
        if self.weekday is not None and not 0 <= self.weekday <= 6:
            raise ValueError(f"row {self.label!r}: weekday must be 0 (Monday) to 6 (Sunday)")

    @classmethod
    def parse(cls, row: BusyRow | Mapping[str, Any]) -> BusyRow:
        """Accept a BusyRow or a mapping such as a row of a UI table:
        `{"label": "Gym", "weekday": "Wed", "start": "19:00", "end": "20:30"}` or
        `{"label": "Dentist", "date": "2026-03-10", "start": "14:00", "end": "15:00"}`.
        """
        if isinstance(row, BusyRow):
            return row
        label = str(row.get("label") or row.get("what") or "Busy").strip()
        weekday = row.get("weekday")
        on = row.get("date", row.get("on"))
        return cls(
            label=label,
            start=_parse_clock(row.get("start"), label),
            end=_parse_clock(row.get("end"), label),
            weekday=_parse_weekday(weekday, label) if _present(weekday) else None,
            on=_parse_date(on, label) if _present(on) else None,
        )


def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):  # an empty cell in a DataFrame
        return False
    return str(value).strip() != ""


def _parse_clock(value, label: str) -> time:
    if isinstance(value, time):
        return value
    text = str(value if value is not None else "").strip()
    if text in ("24:00", "24"):
        return time.max
    try:
        hours, _, minutes = text.partition(":")
        return time(int(hours), int(minutes or 0))
    except ValueError as exc:
        raise ValueError(f"row {label!r}: {text!r} is not a time of day like 09:30") from exc


def _parse_weekday(value, label: str) -> int:
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        return int(value)
    text = str(value).strip().lower()
    for index, name in enumerate(_WEEKDAYS):
        if len(text) >= 2 and name.startswith(text):
            return index
    raise ValueError(f"row {label!r}: {value!r} is not a weekday")


def _parse_date(value, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise ValueError(f"row {label!r}: {value!r} is not a date like 2026-03-10") from exc


def busy_from_table(
    rows: Iterable[BusyRow | dict],
    start_date: date,
    days: int,
    zone_name: str = "UTC",
) -> list[BusyEvent]:
    """The same `BusyEvent`s an `.ics` would give, from hand-typed rows.

    For a student who would rather type "lectures Monday 9 to 11" than export a
    calendar. Occurrences are local wall-clock intervals in `zone_name`, clipped to
    the horizon exactly as `expand_events` clips, so everything downstream
    (the grid, exam detection by keyword, the planner) cannot tell the difference.
    """
    zone = ZoneInfo(zone_name)
    window_start = datetime.combine(start_date, time(0, 0), tzinfo=zone)
    window_end = window_start + timedelta(days=days)
    out: list[BusyEvent] = []
    for raw in rows:
        row = BusyRow.parse(raw)
        if row.on is not None:
            dates = [row.on]
        else:
            # from the day before the horizon, so a night that starts on the eve
            # still blocks the first morning
            dates = [
                start_date + timedelta(days=offset)
                for offset in range(-1, days + 1)
                if (start_date + timedelta(days=offset)).weekday() == row.weekday
            ]
        for day in dates:
            begin = datetime.combine(day, row.start, tzinfo=zone)
            if row.end == time.max:
                finish = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)
            elif row.end <= row.start:
                finish = datetime.combine(day + timedelta(days=1), row.end, tzinfo=zone)
            else:
                finish = datetime.combine(day, row.end, tzinfo=zone)
            if finish <= window_start or begin >= window_end:
                continue
            out.append(BusyEvent(max(begin, window_start), min(finish, window_end), row.label))
    out.sort(key=lambda e: (e.start, e.end))
    return out


# --------------------------------------------------------------------------- #
# Deadlines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Deadline:
    """A test or exam found in the calendar."""

    subject: str
    when: datetime
    summary: str

    def days_from(self, start_date: date) -> float:
        return (self.when - datetime.combine(start_date, time(0, 0), tzinfo=self.when.tzinfo)) / timedelta(
            days=1
        )


def find_deadlines(
    events: Sequence[BusyEvent], keywords: Sequence[str] = DEADLINE_KEYWORDS
) -> list[Deadline]:
    """Pick out events whose title names an assessment.

    Keyword matching, deliberately: it is transparent, multilingual by extension,
    and wrong in ways a user can see and fix by renaming an event. An opaque
    classifier that silently mislabels "Test-driven development lecture" as an
    exam would be worse, not better. Earliest occurrence wins per subject, since
    that is the deadline that binds.

    Keywords match whole words, plural allowed, longest first, so "EXAMEN" is the
    keyword "examen" and never "exam" followed by "EN" (AUDIT.md item 29). The
    subject is the course name: see `_course_name`.
    """
    found: dict[str, Deadline] = {}
    for event in events:
        title = event.summary
        match = _assessment_match(title, tuple(keywords))
        if match is None:
            continue
        subject = _course_name(title, match) or title.strip()
        existing = found.get(subject.casefold())
        if existing is None or event.start < existing.when:
            found[subject.casefold()] = Deadline(subject=subject, when=event.start, summary=title)
    return sorted(found.values(), key=lambda d: d.when)


@functools.lru_cache(maxsize=8)
def _keyword_patterns(keywords: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(
        re.compile(rf"(?<!\w){re.escape(k)}s?(?!\w)", re.IGNORECASE)
        for k in sorted({k.lower() for k in keywords}, key=len, reverse=True)
    )


def _assessment_match(title: str, keywords: tuple[str, ...] = DEADLINE_KEYWORDS) -> re.Match[str] | None:
    return next((m for p in _keyword_patterns(keywords) if (m := p.search(title))), None)


def course_of(title: str) -> str:
    """The course a timetable event belongs to, or "" if the title names none.

    The same reading of a title as `_course_name`, without an assessment keyword:
    the first field that contains a letter, "Algebra 3" in "Algebra 3, Grp: CM .,
    Salle: 4", less a word that only says what kind of session it is, "Analysis"
    in "Analysis lecture" or "Fisica" in "Lezione di Fisica". A lecture belongs to
    a subject when this equals the subject's name, ignoring case.
    """
    for field in _FIELD_SEPARATORS.split(title):
        words = field.strip(" .-–—:·").split()
        if not any(c.isalpha() for c in "".join(words)):
            continue
        if words[0].casefold().rstrip(":") in ("grp", "group", "groupe", "salle", "room"):
            return ""
        while words and words[-1].casefold() in _SESSION_WORDS:
            words.pop()
        while words and words[0].casefold() in _SESSION_WORDS:
            words.pop(0)
            if len(words) > 1 and words[0].casefold() in _CONNECTIVES:
                words.pop(0)
        if words:
            return " ".join(words)
    return ""


def find_lectures(
    events: Sequence[BusyEvent], keywords: Sequence[str] = DEADLINE_KEYWORDS
) -> list[tuple[str, BusyEvent]]:
    """Every event that is not an assessment, with the course it belongs to.

    Nothing here decides that an event *is* teaching: gym sessions come back as
    course "Gym". The service keeps only the events whose course is the name of a
    subject being planned, which is what makes a weekly "Gym" harmless.
    """
    out = []
    for event in events:
        if _assessment_match(event.summary, tuple(keywords)) is not None:
            continue
        course = course_of(event.summary)
        if course:
            out.append((course, event))
    return out


# Words that name the kind of session rather than the course.
_SESSION_WORDS = (
    "lecture",
    "lectures",
    "lesson",
    "lessons",
    "class",
    "seminar",
    "tutorial",
    "lab",
    "cours",
    "lezione",
    "lezioni",
    "vorlesung",
    "übung",
    "cm",
    "td",
    "tp",
)

# "COURS ANNULE", "annulée", "cancelled", "annullata", "entfällt": a session that
# is not happening, said in its title.
_CANCELLED_TITLE = re.compile(
    r"(?<!\w)(annul[ée]e?s?|cancell?ed|annullat[oaie]|entf[äa]llt|abgesagt)(?!\w)", re.IGNORECASE
)


# Connectives left behind when the keyword is removed: "Esame di Fisica",
# "Examen de physique", "Exam of Analysis".
_CONNECTIVES = ("di", "del", "della", "dello", "de", "du", "des", "of", "in", "für", "d'")

# What separates the fields of a decorated title: "Course, Grp: TYPE ., Salle: Room"
# (ADE and Hyperplanning exports), "EXAMEN - Course | Room", "Exam: Course, Room".
# A dash separates only with spaces around it, so "Semi-supervised learning" stays whole.
_FIELD_SEPARATORS = re.compile(r"\s*[,;|]\s*|\s+[-–—]\s+|:\s+")


def _course_name(title: str, keyword: re.Match[str]) -> str:
    """The course an assessment title is about.

    Remove the keyword, split what remains into fields, and take the first field
    that contains a letter. University timetable exports decorate titles with the
    group and the room, and the course name comes first; a hand-made "Analysis
    exam" has one field. A course name that itself contains a comma loses its tail,
    a visible mistake the user can correct in the subjects table.
    """
    remainder = title[: keyword.start()] + " " + title[keyword.end() :]
    fields = [f.strip(" .-–—:·") for f in _FIELD_SEPARATORS.split(remainder)]
    field = next((f for f in fields if any(c.isalpha() for c in f)), "")
    for label in ("grp", "group", "groupe", "salle", "room"):
        if field.lower().startswith(label + ":"):
            return ""
    head, _, tail = field.partition(" ")
    if tail and head.lower() in _CONNECTIVES:
        field = tail.strip()
    elif field.lower().startswith("d'") and len(field) > 2:
        field = field[2:].strip()
    return " ".join(field.split())


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def plan_to_ics(
    sessions: Sequence[tuple[int, str, str]],
    start_date: date,
    zone_name: str = "UTC",
    slots_per_day: int = SLOTS_PER_DAY,
    block_slots: int = 3,
    calendar_name: str = "Study plan",
) -> str:
    """Turn scheduled blocks into an importable calendar.

    `sessions` is (absolute slot index, subject, rationale). The rationale goes
    into the event description on purpose: a schedule a student does not
    understand is a schedule they will not follow, and "review 3 of 4 — timed for
    85% recall, the point where a review is worth most" is the difference between
    an instruction and an explanation.
    """
    zone = ZoneInfo(zone_name)
    minutes_per_slot = 24 * 60 // slots_per_day
    calendar = Calendar()
    calendar.add("prodid", "-//constrained-path-scheduler//EN")
    calendar.add("version", "2.0")
    calendar.add("x-wr-calname", calendar_name)

    midnight = datetime.combine(start_date, time(0, 0), tzinfo=zone)
    for slot, subject, rationale in sessions:
        begin = midnight + timedelta(minutes=slot * minutes_per_slot)
        event = Event()
        event.add("summary", f"Study: {subject}")
        event.add("dtstart", begin)
        event.add("dtend", begin + timedelta(minutes=block_slots * minutes_per_slot))
        event.add("description", rationale)
        event.add("uid", f"cps-{slot}-{subject.replace(' ', '-').lower()}@constrained-path-scheduler")
        event.add("dtstamp", datetime.now(UTC))
        calendar.add_component(event)
    # RFC 5545 requires a VTIMEZONE for every TZID the events reference. Google
    # and Apple resolve a bare IANA name anyway; Outlook does not reliably, and an
    # importer that cannot resolve it may shift every block (AUDIT.md item 24).
    calendar.add_missing_timezones()
    return calendar.to_ical().decode("utf-8")
