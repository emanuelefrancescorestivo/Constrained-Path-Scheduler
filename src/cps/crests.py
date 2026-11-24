"""
Crests (DECISIONS.md D189): points add up to a level, badges climb four tiers, and a
friendly weekly league among friends. Pure functions of plain values; `service` feeds
them what a student did.

The owner chose them from a canvas of options (2026-11-24): the crest (a six-sided
shield, its metal its tier or league), levels and leagues together ("it's fine to have
leagues, it's a friendly game"), each row of the league the person's own avatar, and a
collection of badges each earned with something the app already counts.

- **Level**: every point ever earned (D158's +10 a session, +20 once vouched for, and
  `TIER_POINTS` for each badge tier, `RARE_POINTS` for a rare one) makes the level,
  which never goes down. Level n starts at 50·n·(n−1) points: 100 more each level.
- **Badges**: twenty families, each with four thresholds (bronze, silver, gold, ice),
  and four rare ones of a single tier. A family counts events (each self-test, each
  card answered…) or a best (the longest run of days, the longest timed session).
- **League**: a week, Monday to Sunday, among the student and the friends who play
  (people who follow each other). At the week's end the top `LEAGUE_MOVE` move up a
  league and the bottom `LEAGUE_MOVE` move down, unless they studied on
  `LEAGUE_FLOOR_DAYS` days of it; a table smaller than `LEAGUE_LEAST` moves no one.

Rejected: points for hours (the badges were never for hours, D41); a league among
strangers (the owner's is among friends); demotion for a week of study, however few
points (a friendly game keeps those who kept studying). Every threshold and name is a
choice, not measured.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

LEVEL_NAMES = ("Spark", "Neuron", "Synapse", "Circuit", "Cortex", "Network", "Insight", "Mastery")
LEVEL_STEP = 100  # each level asks this many points more than the one before
TIER_POINTS = 50  # a badge tier earned
RARE_POINTS = 100  # a rare badge earned
TIERS = ("bronze", "silver", "gold", "ice")
TIER_ROMAN = ("I", "II", "III", "IV")

LEAGUES = ("bronze", "silver", "gold", "ice", "aurora")
LEAGUE_MOVE = 3  # the top three move up, the bottom three down
LEAGUE_LEAST = 4  # a smaller table moves no one
LEAGUE_FLOOR_DAYS = 3  # days studied in the week that keep a student from moving down
LEAGUE_SIZE = 10  # the student and up to nine friends
PROOF_A_DAY = 1  # vouched sessions that count their +20 in the league, each day
LOGGED_A_DAY = 3  # sessions of the diary outside the plan that earn their +10, each day
UNDATED = date.min  # a tier earned on a day not kept (a task's steps, a pair's best run)


def level_start(n: int) -> int:
    """The points at which level `n` (from 1) begins: 0, 100, 300, 600, 1,000…"""
    return LEVEL_STEP * n * (n - 1) // 2


def level_of(points: int) -> dict:
    """The level `points` reach: its number, its name (the last name repeats past it),
    where it began and where the next begins, and how far along (whole percents)."""
    points = max(0, int(points))
    n = 1
    while level_start(n + 1) <= points:
        n += 1
    start, end = level_start(n), level_start(n + 1)
    return {
        "n": n,
        "name": LEVEL_NAMES[min(n, len(LEVEL_NAMES)) - 1],
        "next_name": LEVEL_NAMES[min(n + 1, len(LEVEL_NAMES)) - 1],
        "points": points,
        "start": start,
        "next": end,
        "left": end - points,
        "percent": round(100 * (points - start) / (end - start)),
    }


@dataclass(frozen=True)
class Family:
    """A badge that climbs four tiers: its key, name, what earns it (one sentence),
    its icon (a Lucide name), its group, the four thresholds and how they read."""

    key: str
    name: str
    how: str
    icon: str
    group: str
    thresholds: tuple[int, int, int, int]
    unit: str  # "{n} days", for the ladder's last number; "" for bare numbers


@dataclass(frozen=True)
class Rare:
    key: str
    name: str
    how: str
    icon: str


GROUPS = (
    ("habit", "Habit", "coming back, day after day"),
    ("memory", "Memory", "recall, not rereading"),
    ("focus", "Focus", "time with the timer, steps ticked"),
    ("together", "Together", "friends, notes, cheers and checks"),
)

FAMILIES = (
    Family(
        "streak", "On a roll", "Days in a row with a session.", "flame", "habit", (3, 7, 30, 100), "{n} days"
    ),
    Family(
        "full_week",
        "Full week",
        "Every session of a week done.",
        "calendar-check",
        "habit",
        (1, 4, 8, 12),
        "{n} weeks",
    ),
    Family(
        "on_time",
        "On time",
        "Sessions started on time with the timer.",
        "alarm-clock",
        "habit",
        (5, 25, 100, 250),
        "",
    ),
    Family(
        "comeback",
        "Comeback",
        "A missed day repaired with a make-up session.",
        "repeat",
        "habit",
        (1, 3, 10, 25),
        "",
    ),
    Family("seasons", "Seasons", "Semesters planned with the app.", "leaf", "habit", (2, 3, 4, 6), ""),
    Family("recall", "Recall", "Self-tests answered.", "history", "memory", (10, 50, 150, 500), ""),
    Family(
        "long_memory",
        "Long memory",
        "Topics that hold past a month.",
        "brain",
        "memory",
        (1, 10, 50, 150),
        "",
    ),
    Family("cards", "Card master", "Flashcards answered.", "layers", "memory", (100, 500, 2000, 5000), ""),
    Family(
        "practice", "Exam rehearsal", "Timed exam practice done.", "hourglass", "memory", (1, 3, 10, 25), ""
    ),
    Family("exams", "Exam done", "Exams you said you passed.", "graduation-cap", "memory", (1, 3, 6, 12), ""),
    Family("focus", "Focus", "Sessions timed with Focus.", "timer", "focus", (5, 25, 100, 250), ""),
    Family(
        "deep_focus",
        "Deep focus",
        "Your longest session with the timer.",
        "target",
        "focus",
        (30, 60, 105, 150),
        "minutes",
    ),
    Family("steps", "Step by step", "Tasks finished in steps.", "list-checks", "focus", (1, 10, 50, 150), ""),
    Family(
        "explorer",
        "Explorer",
        "Sessions at a place on the study map.",
        "map-pin",
        "focus",
        (1, 10, 50, 150),
        "",
    ),
    Family(
        "buddy",
        "Study buddy",
        "Days in a row studied with a friend.",
        "users",
        "together",
        (3, 7, 30, 100),
        "{n} days",
    ),
    Family(
        "teacher", "Teacher", "Explanations shared.", "message-square-text", "together", (1, 5, 20, 50), ""
    ),
    Family(
        "notes",
        "Note sharer",
        "Helpful marks your notes received.",
        "book-open-text",
        "together",
        (1, 5, 20, 50),
        "",
    ),
    Family(
        "checker",
        "Fact checker",
        "Checks and vouches for classmates.",
        "shield-check",
        "together",
        (5, 25, 100, 250),
        "",
    ),
    Family(
        "cheers",
        "Cheerleader",
        "Cheers sent to someone studying.",
        "message-square-heart",
        "together",
        (5, 25, 100, 250),
        "",
    ),
    Family(
        "team",
        "Team player",
        "Weeks your study group met its goal.",
        "users-round",
        "together",
        (1, 4, 12, 26),
        "",
    ),
)

RARES = (
    Rare("first", "First step", "Your first session done.", "sparkles"),
    Rare(
        "odds", "Against the odds", "A topic recalled that the model gave less than even odds.", "lightbulb"
    ),
    Rare("champion", "League champion", "First in your league for a week.", "trophy"),
    Rare("sweep", "Clean sweep", "A week in which you recalled every self-test you answered.", "telescope"),
)


def tiers_from_events(days: Iterable[date], thresholds: Sequence[int]) -> list[date | None]:
    """Each tier's day from events counted one by one: the day of the event that
    reached its threshold, or None while it is not reached. An event on `UNDATED`
    counts, and a tier it reaches is earned on no day the page can say."""
    ordered = sorted(days)
    dated = [d for d in ordered if d != UNDATED]
    undated = len(ordered) - len(dated)
    out: list[date | None] = []
    for t in thresholds:
        if len(ordered) < t:
            out.append(None)
        elif t <= undated:
            out.append(UNDATED)
        else:
            out.append(dated[t - undated - 1])
    return out


def tiers_from_best(firsts: Mapping[int, date | None], thresholds: Sequence[int]) -> list[date | None]:
    """Each tier's day from a best that grew (a run of days, a session's length): the
    first day it reached each threshold, as `firsts` gives them."""
    return [firsts.get(t) for t in thresholds]


def tier_of(days: Sequence[date | None]) -> int:
    """How many tiers are earned (0 to 4), tiers being earned in order."""
    n = 0
    for day in days:
        if day is None:
            break
        n += 1
    return n


def ladder(family: Family) -> str:
    """The four thresholds as one line: "3 · 7 · 30 · 100 days"."""
    numbers = " · ".join(f"{t:,}" for t in family.thresholds)
    return family.unit.replace("{n}", numbers) if "{n}" in family.unit else numbers


def league_move(rank: int, size: int, days_studied: int) -> int:
    """What a week's rank does to the league (+1 up, -1 down, 0 stays): the top
    `LEAGUE_MOVE` up and the bottom `LEAGUE_MOVE` down, unless they studied on
    `LEAGUE_FLOOR_DAYS` days of the week; a table under `LEAGUE_LEAST` moves no one.
    Ranks start at 1; with six or fewer, the top three move up and nobody down."""
    if size < LEAGUE_LEAST:
        return 0
    if rank <= LEAGUE_MOVE:
        return 1
    if size > 2 * LEAGUE_MOVE and rank > size - LEAGUE_MOVE and days_studied < LEAGUE_FLOOR_DAYS:
        return -1
    return 0


def next_league(tier: int, move: int) -> int:
    return max(0, min(len(LEAGUES) - 1, tier + move))


def ranking(points: Mapping[str, int], names: Mapping[str, str]) -> list[str]:
    """The table's order: most points first, then by name, so a tie is told apart
    the same way for everyone."""
    return sorted(points, key=lambda who: (-points[who], names.get(who, who).casefold()))
