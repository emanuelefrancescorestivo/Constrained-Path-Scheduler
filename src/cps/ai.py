"""
AI features (DECISIONS.md D12): reading a task typed in words, and writing the
weekly review's paragraph.

Each feature has a version made of rules that always answers. A model is asked
only when four things hold: the server has an API key, the student turned AI on,
the month's spending is under the cap, and the student is under the day's limit.
Anything else, any error included, gives the rules' answer, so a student never
sees more than a plainer result.

The model is reached through one small interface, `Provider`. `AnthropicProvider`
implements it with Anthropic's official SDK (`anthropic`, in the optional `ai`
extra), imported only when a key is set; the tests use a fake. What is sent is what
the privacy page names: the sentence, today's date, the time zone and the course
names; for the review, its numbers and the course and exam names. Never the plan's
token, its timetable, or anything from the network.
"""

from __future__ import annotations

import functools
import json
import os
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

from .store import Store

# --------------------------------------------------------------------------- #
# Reading a task typed in words, by rules (English and French)
# --------------------------------------------------------------------------- #

WEEKDAYS = {
    "monday": 0, "lundi": 0,
    "tuesday": 1, "tues": 1, "mardi": 1,
    "wednesday": 2, "mercredi": 2,
    "thursday": 3, "thurs": 3, "jeudi": 3,
    "friday": 4, "vendredi": 4,
    "saturday": 5, "samedi": 5,
    "sunday": 6, "dimanche": 6,
}  # fmt: skip
# Three-letter weekdays are left out: "mon" is French for "my", "sun" and "sat"
# are words, "mar" is March as well as mardi.
MONTHS = {
    "january": 1, "jan": 1, "janvier": 1, "janv": 1,
    "february": 2, "feb": 2, "fevrier": 2, "fev": 2,
    "march": 3, "mars": 3,
    "april": 4, "apr": 4, "avril": 4, "avr": 4,
    "may": 5, "mai": 5,
    "june": 6, "jun": 6, "juin": 6,
    "july": 7, "jul": 7, "juillet": 7, "juil": 7,
    "august": 8, "aug": 8, "aout": 8,
    "september": 9, "sep": 9, "sept": 9, "septembre": 9,
    "october": 10, "oct": 10, "octobre": 10,
    "november": 11, "nov": 11, "novembre": 11,
    "december": 12, "dec": 12, "decembre": 12,
}  # fmt: skip
END_OF_DAY = time(23, 59)  # a deadline "on" a day is the end of it, as the sheet's shortcuts say

# Words that join a date or a duration to the rest of the sentence ("for Friday",
# "pour vendredi", "about 6 h"), taken away with it.
_LEAD = (
    r"(?:\b(?:for|due|by|on|before|until|till|about|around|approx\.?|roughly|"
    r"pour|le|la|avant|vers|d'ici|environ|a rendre|a faire|de)\s+|~\s*)*"
)
_NUM = r"(\d+(?:[.,]\d+)?)"


def _fold(c: str, accents: bool = False) -> str:
    """One character, lower case and (unless `accents`) without its accent; always
    one character, so that positions in the folded text are positions in the
    sentence as typed."""
    if c == "’":
        return "'"
    low = c.lower()[:1] or c
    if accents:
        return low
    base = [x for x in unicodedata.normalize("NFD", low) if not unicodedata.combining(x)]
    return base[0] if len(base) == 1 else low


def _plain(text: str) -> str:
    """Lower case, accents removed: "Août" and "aout" are one word here."""
    return "".join(_fold(c) for c in text)


@dataclass(frozen=True)
class TaskGuess:
    """What a sentence says about a task. Empty fields were not found."""

    name: str
    due: str  # local "YYYY-MM-DDTHH:MM", as the new-task form takes it, or ""
    hours: float | None
    course: str

    def as_dict(self) -> dict:
        return {"name": self.name, "due": self.due, "hours": self.hours, "course": self.course}


def _next_weekday(today: date, weekday: int) -> date:
    """The next such day after today ("Friday" said on a Friday is next week's)."""
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def _dated(today: date, day: int, month: int, year: int | None) -> date | None:
    """A day and month; without a year, the next one from today."""
    try:
        if year is not None:
            return date(year + 2000 if year < 100 else year, month, day)
        found = date(today.year, month, day)
        return found if found >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def read_task(words: str, *, today: date, courses: Sequence[str] = ()) -> TaskGuess:
    """A task from one line, by rules: "stats report for Friday, about 6 h",
    "rapport de stats pour vendredi 18h, 6 heures", "essay due 12/10 (4 hours)".

    Dates: today, tomorrow, the day after, in N days or weeks, next week, a weekday
    (its next occurrence after today), "12 October", "October 12", "12/10" (day
    first, unless the first number cannot be a month), "2026-10-12". Times: "18:00",
    "18h30", "at 6pm", "à 18h", noon, midnight; otherwise the end of the day. Work:
    "6 h", "6 hours", "6 heures", "90 min", "an hour". The course is the one of the
    student's whose words the sentence shares (four letters in common at the start
    of a word: "stats" finds "Advanced Statistics"); none if two tie."""
    typed = " " + " ".join(words.split()) + " "
    text = _plain(typed)
    accented = "".join(_fold(c, accents=True) for c in typed)
    found_day: date | None = None
    found_time: time | None = None
    hours: float | None = None
    spans: list[tuple[int, int]] = []
    left = [text, accented]  # what is still to read: a piece read once is blanked out

    def mark(start: int, end: int) -> None:
        spans.append((start, end))
        for i, version in enumerate(left):
            left[i] = version[:start] + " " * (end - start) + version[end:]

    def take(pattern: str, accents: bool = False) -> re.Match[str] | None:
        match = re.search(pattern, left[1] if accents else left[0])
        if match:
            mark(*match.span())
        return match

    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    weekdays = "|".join(WEEKDAYS)
    a_day = (
        rf"(?:{weekdays}|tomorrow|demain|today|aujourd'hui|\d{{1,2}}[/.]\d{{1,2}}|\d{{1,2}}\s+(?:{months}))"
    )
    # Times of day first, so that "18h" is not read as eighteen hours of work: "at
    # 18:00", "à 18h" (with its accent: English "a 2h session" is two hours of work),
    # "18h30", "6pm", and an hour written straight after a day ("vendredi 18h").
    if m := take(_LEAD + r"(?:\bat\s+|(?<!\w)à\s+)(\d{1,2})\s*(?:h|:)\s*(\d{2})?\b", accents=True):
        found_time = time(int(m.group(1)) % 24, int(m.group(2) or 0))
    elif m := re.search(rf"\b{a_day}\s+(\d{{1,2}})h(\d{{2}})?\b", left[0]):
        mark(m.start(1), m.end())
        found_time = time(int(m.group(1)) % 24, int(m.group(2) or 0))
    elif m := take(_LEAD + r"\b(\d{1,2})(?::|h)(\d{2})\b"):
        found_time = time(int(m.group(1)) % 24, int(m.group(2)) % 60)
    elif m := take(_LEAD + r"\b(?:at\s+)?(\d{1,2})\s*(am|pm)\b"):
        hour = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0)
        found_time = time(hour, 0)
    elif take(_LEAD + r"\b(?:noon|midi)\b"):
        found_time = time(12, 0)
    elif take(_LEAD + r"\b(?:midnight|minuit)\b"):
        found_time = END_OF_DAY

    if m := take(_LEAD + r"\b" + _NUM + r"\s*(?:min|mins|minutes?)\b"):
        hours = float(m.group(1).replace(",", ".")) / 60
    elif m := take(_LEAD + r"\b" + _NUM + r"\s*(?:h|hrs?|hours?|heures?)\b(?:\s+(?:of work|de travail))?"):
        hours = float(m.group(1).replace(",", "."))
    elif take(_LEAD + r"\b(?:an hour|one hour|une heure|1 heure)\b"):
        hours = 1.0

    if m := take(_LEAD + r"\b(\d{4})-(\d{2})-(\d{2})\b"):
        found_day = _dated(today, int(m.group(3)), int(m.group(2)), int(m.group(1)))
    elif m := take(_LEAD + r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b"):
        first, second = int(m.group(1)), int(m.group(2))
        day, month = (second, first) if first <= 12 < second else (first, second)
        found_day = _dated(today, day, month, int(m.group(3)) if m.group(3) else None)
    elif m := take(_LEAD + rf"\b(\d{{1,2}})(?:er|st|nd|rd|th)?\s+({months})\.?\b(?:\s+(\d{{4}}))?"):
        found_day = _dated(
            today, int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)) if m.group(3) else None
        )
    elif m := take(_LEAD + rf"\b({months})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?"):
        found_day = _dated(
            today, int(m.group(2)), MONTHS[m.group(1)], int(m.group(3)) if m.group(3) else None
        )
    elif take(_LEAD + r"\b(?:day after tomorrow|apres-demain|apres demain)\b"):
        found_day = today + timedelta(days=2)
    elif take(_LEAD + r"\b(?:tomorrow|demain)\b"):
        found_day = today + timedelta(days=1)
    elif take(_LEAD + r"\b(?:today|tonight|aujourd'hui|ce soir)\b"):
        found_day = today
    elif m := take(_LEAD + r"\b(?:in|dans)\s+(\d+)\s+(days?|jours?|weeks?|semaines?)\b"):
        n = int(m.group(1))
        found_day = today + timedelta(days=n * (7 if m.group(2)[0] in "ws" else 1))
    elif take(_LEAD + r"\b(?:next week|la semaine prochaine|semaine prochaine)\b"):
        found_day = today + timedelta(days=7)
    elif m := take(_LEAD + rf"\b(?:next\s+|this\s+|ce\s+)?({weekdays})(?:\s+(?:prochain|next))?\b"):
        found_day = _next_weekday(today, WEEKDAYS[m.group(1)])

    # The name: the sentence without what was read, in the student's own letters.
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    kept, last = [], 0
    for start, end in merged:
        kept.append(typed[last:start])
        last = end
    kept.append(typed[last:])
    name = re.sub(r"\s+", " ", " ".join(kept))
    name = re.sub(r"\s+([,.;:)])", r"\1", name)
    name = re.sub(r"\(\s*\)", "", name).strip(" ,.;:-–—(")
    name = re.sub(r"(?i)\s+(for|due|by|on|pour|le|avant|d'ici|à rendre)$", "", name).strip(" ,.;:-–—")
    name = name[:1].upper() + name[1:]

    due = ""
    if found_day is not None:
        due = datetime.combine(found_day, found_time or END_OF_DAY).strftime("%Y-%m-%dT%H:%M")
    return TaskGuess(name=name[:120], due=due, hours=hours, course=_course(text, courses))


# Words that say what kind of work it is, not which course.
_STOP = frozenset(
    {
        "the", "for", "and", "des", "les", "une", "pour", "avec", "report", "rapport", "sheet",
        "exercise", "exercises", "homework", "devoir", "devoirs", "project", "projet", "essay",
        "exam", "examen", "read", "reading", "lecture", "chapter", "chapitre",
    }
)  # fmt: skip


def _course(text: str, courses: Sequence[str]) -> str:
    said = [w for w in re.findall(r"[a-z]{3,}", text) if w not in _STOP]
    best, best_score, tie = "", 0, False
    for course in courses:
        names = [w for w in re.findall(r"[a-z]{3,}", _plain(course)) if w not in _STOP]
        score = sum(
            1 for w in said if any(w == n or (len(w) >= 4 and len(n) >= 4 and w[:4] == n[:4]) for n in names)
        )
        if score > best_score:
            best, best_score, tie = course, score, False
        elif score and score == best_score:
            tie = True
    return "" if tie else best


# --------------------------------------------------------------------------- #
# The model: one interface, Anthropic's SDK behind it
# --------------------------------------------------------------------------- #

# US dollars per million input and output tokens: Anthropic's price list as cached
# in the claude-api reference on 2026-10-06 (D12). A model not listed here is never
# called, since its calls could not be counted against the cap.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MONTHLY_CAP = 10.0  # USD
DEFAULT_DAILY_CALLS = 20  # per student


class AIError(Exception):
    """The model could not give a usable answer; the rules answer instead."""


@dataclass(frozen=True)
class Answer:
    data: dict | None  # None: the call ended without a usable answer (and may still cost)
    model: str
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    model: str

    def ask(self, system: str, prompt: str, schema: dict, max_tokens: int) -> Answer: ...


class AnthropicProvider:
    """Claude through the official SDK: an answer constrained by a JSON schema
    (structured outputs), at low effort, with a short timeout and one retry."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 20.0) -> None:
        import anthropic  # the optional `ai` extra; only imported when a key is set

        self.model = model
        self._errors: tuple[type[Exception], ...] = (anthropic.APIError,)
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=1)

    def ask(self, system: str, prompt: str, schema: dict, max_tokens: int) -> Answer:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            )
        except self._errors as error:
            raise AIError(type(error).__name__) from None
        usage = response.usage
        tokens_in = (
            usage.input_tokens
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        data = None
        # A refusal, or an answer cut by max_tokens, is not used (the rules answer).
        if response.stop_reason == "end_turn":
            text = next((b.text for b in response.content if b.type == "text"), "")
            try:
                parsed = json.loads(text)
                data = parsed if isinstance(parsed, dict) else None
            except ValueError:
                data = None
        return Answer(data, self.model, tokens_in, usage.output_tokens)


@functools.lru_cache(maxsize=1)
def from_env() -> Provider | None:
    """The server's provider: `ANTHROPIC_API_KEY` and `CPS_AI_MODEL`. None without a
    key, without the `ai` extra, or with a model whose price is not known."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("CPS_AI_MODEL", "").strip() or DEFAULT_MODEL
    if not key or model not in PRICES:
        return None
    try:
        return AnthropicProvider(key, model)
    except ImportError:
        return None


def monthly_cap() -> float:
    try:
        return float(os.environ.get("CPS_AI_MONTHLY_CAP_USD") or DEFAULT_MONTHLY_CAP)
    except ValueError:
        return DEFAULT_MONTHLY_CAP


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


# --------------------------------------------------------------------------- #
# The ledger: every call's tokens and cost, the monthly cap, the daily limit
# --------------------------------------------------------------------------- #


def _month_start(now: datetime) -> str:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def spent_this_month(store: Store, now: datetime) -> float:
    with store.connection() as db:
        (total,) = db.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM ai_usage WHERE created >= ?", (_month_start(now),)
        ).fetchone()
    return float(total)


def calls_today(store: Store, token: str, now: datetime) -> int:
    since = (now.astimezone(UTC) - timedelta(days=1)).isoformat()
    with store.connection() as db:
        (n,) = db.execute(
            "SELECT COUNT(*) FROM ai_usage WHERE token = ? AND created >= ?", (token, since)
        ).fetchone()
    return int(n)


def usage(store: Store, since: datetime) -> dict:
    """Calls, answers used and dollars since `since`, for the pilot's measures."""
    with store.connection() as db:
        calls, used, dollars = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok), 0), COALESCE(SUM(cost), 0) FROM ai_usage WHERE created >= ?",
            (since.astimezone(UTC).isoformat(),),
        ).fetchone()
    return {"calls": int(calls), "used": int(used), "dollars": round(float(dollars), 4)}


def _record(store: Store, token: str, feature: str, answer: Answer | None, model: str, now: datetime) -> None:
    tokens_in, tokens_out = (answer.input_tokens, answer.output_tokens) if answer else (0, 0)
    with store.connection() as db:
        db.execute(
            "INSERT INTO ai_usage (created, token, feature, model, input_tokens, output_tokens, cost, ok) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now.astimezone(UTC).isoformat(timespec="seconds"),
                token,
                feature,
                model,
                tokens_in,
                tokens_out,
                cost(model, tokens_in, tokens_out),
                int(bool(answer and answer.data is not None)),
            ),
        )


def ask(
    store: Store,
    token: str,
    provider: Provider,
    feature: str,
    *,
    system: str,
    prompt: str,
    schema: dict,
    max_tokens: int,
    now: datetime,
    cap: float | None = None,
    daily: int = DEFAULT_DAILY_CALLS,
) -> dict | None:
    """One call, if the cap and the day's limit allow it: the cap is checked
    against the month's spending plus the call's worst case (every byte of the
    prompt a token, every output token used). None when not allowed or not usable."""
    if provider.model not in PRICES:
        return None
    worst = cost(provider.model, len((system + prompt).encode()), max_tokens)
    if spent_this_month(store, now) + worst > (monthly_cap() if cap is None else cap):
        return None
    if calls_today(store, token, now) >= daily:
        return None
    answer: Answer | None = None
    try:
        answer = provider.ask(system, prompt, schema, max_tokens)
    except Exception:  # any failure of the model is the rules' turn
        answer = None
    _record(store, token, feature, answer, provider.model, now)
    return answer.data if answer else None


# --------------------------------------------------------------------------- #
# The two features' prompts, and what is accepted back
# --------------------------------------------------------------------------- #

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "due": {"type": "string"},
        "hours": {"type": "number"},
        "course": {"type": "string"},
    },
    "required": ["name", "due", "hours", "course"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {"paragraph": {"type": "string"}},
    "required": ["paragraph"],
    "additionalProperties": False,
}


def task_prompt(words: str, now: datetime, tz: str, courses: Sequence[str]) -> tuple[str, str]:
    system = (
        "You read one line a university student typed about a piece of coursework and return it as "
        "fields for their study planner. name: a short title in the student's own language, without "
        "the date or the hours. due: the deadline as local time YYYY-MM-DDTHH:MM; a weekday means its "
        "next occurrence after today; with no time, use 23:59; with no date at all, an empty string. "
        "hours: the hours of work the student said, or 0 if they did not say. course: exactly one "
        "of the student's courses if the line is clearly about it, else an empty string."
    )
    prompt = json.dumps(
        {
            "today": now.strftime("%A %Y-%m-%d %H:%M"),
            "time_zone": tz,
            "courses": list(courses),
            "line": words,
        },
        ensure_ascii=False,
    )
    return system, prompt


def task_from_answer(data: dict, *, today: date, courses: Sequence[str]) -> TaskGuess | None:
    """The model's fields, if they make sense: a date from today to a year ahead,
    hours from 0 to 200, a course from the list. Otherwise None (the rules'
    answer is used)."""
    try:
        name = str(data["name"]).strip()[:120]
        due = str(data["due"]).strip()
        hours = float(data["hours"])
        course = str(data["course"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not name or not 0 <= hours <= 200:
        return None
    if due:
        try:
            when = datetime.strptime(due, "%Y-%m-%dT%H:%M")
        except ValueError:
            return None
        if not today <= when.date() <= today + timedelta(days=366):
            return None
    known = {c.casefold(): c for c in courses}
    return TaskGuess(name=name, due=due, hours=hours or None, course=known.get(course.casefold(), ""))


def review_prompt(review: dict, language: str) -> tuple[str, str]:
    system = (
        f"You write the weekly review in a study planner: one short paragraph, at most 70 words, in "
        f"{language}, to a university student about the week that ended. Second person, plain and "
        "warm. No exclamation marks, no emoji, no blame, nothing about a lost streak. Use only the "
        "numbers given, and do not invent any. End with the suggestion given, in your own words."
    )
    keys = (
        "label", "sessions_planned", "sessions_done", "skipped", "waiting", "hours_done",
        "days_studied", "streak", "deadlines", "deadlines_met", "exams_soon", "logged",
        "hours_logged", "suggestion",
    )  # fmt: skip
    return system, json.dumps({k: review.get(k) for k in keys}, ensure_ascii=False)


def paragraph_from_answer(data: dict) -> str | None:
    text = str(data.get("paragraph", "")).strip()
    return text if 20 <= len(text) <= 700 else None


def fake_answer(data: dict[str, Any] | None, model: str = DEFAULT_MODEL) -> Answer:
    """An answer as a provider gives it, for tests and for trying the pages."""
    return Answer(data, model, 300, 80)
