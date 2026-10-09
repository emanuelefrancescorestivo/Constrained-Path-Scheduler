"""
The demo (`cps demo`): the hosted app on a made-up class, to look around and give
feedback. A shared world of five classmates at other universities (sessions,
explanations, notes, kudos, a study group), and students added to it: @alex for a
demo on one's own computer, or one fresh student per visitor on the public demo
(`cps demo --public`, DECISIONS.md D31). A student comes with weeks of history on
the sample timetable (`examples/sample-semester.ics`), follows and a follow request,
a study group with three teammates, an invitation, and flashcards due.

The sample timetable is the autumn semester 2026-27. The demo moves it by whole
weeks (dates only: weekdays, times, titles and exams stay) so that today is always
in its eighth week, with history behind and exams ahead, on the real clock: a
visitor who tries their own timetable on the same server sees it planned for today.

Everything is invented and made through `cps.service`, as the pages would make it;
nothing in it is a fixture for a number in the documents.
"""

from __future__ import annotations

import contextlib
import json
import random
import re
import secrets
import struct
import threading
import zlib
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from . import service, social
from .store import Store

MARKER = "showcase.json"
TEMPLATE = "semester.json"  # every new student's semester so far, made once (D31)
FIRST = date(2026, 9, 28)  # the sample semester's first Monday
EIGHTH = date(2026, 11, 16)  # the Monday of its eighth week: where today falls
COURSES = ("Algebra 3", "Advanced Statistics", "Analysis 3")
PEOPLE = (
    ("marco", "Politecnico di Milano", "Ingegneria Fisica", "Fisica 2", ""),
    ("lena", "TU München", "B.Sc. Informatik", "Algorithmen und Datenstrukturen", "Algorithms are my thing."),
    ("sofia", "Universidad de Salamanca", "Grado en Historia", "Historia Medieval", ""),
    ("noah", "KU Leuven", "Bachelor Psychologie", "Cognitive Psychology", ""),
    ("chloe", "Sorbonne Université", "Licence Physique", "Mécanique quantique", "Coffee, then quanta."),
)
TEAMMATES = ("maya", "tom", "ines")
ADJECTIVES = ("calm", "brave", "quick", "bright", "keen", "bold", "wise", "sunny", "clever", "merry")
ANIMALS = ("otter", "fox", "owl", "lynx", "panda", "heron", "koala", "tiger", "whale", "robin")


def sample_path() -> Path:
    """The sample timetable: next to the package in a checkout (an editable install),
    or in the working directory (a host that installs the package and starts the
    command from the repository)."""
    for root in (Path(__file__).resolve().parents[2], Path.cwd()):
        found = root / "examples" / "sample-semester.ics"
        if found.exists():
            return found
    raise FileNotFoundError("the demo needs the repository's examples/sample-semester.ics")


@dataclass(frozen=True)
class Showcase:
    """What `cps demo` prints and runs on: the classmates, the review page's key, how
    far the timetable was moved, and the student made for this computer, if any."""

    people: dict[str, str]  # handle -> token
    crew: str  # chloe's group, which invites each new student while it has room
    admin: str  # the review page's key, for this demo store only
    weeks: int  # the sample timetable moved by this many weeks
    you: str | None = None  # @alex's plan token, on a demo of one's own
    group: str | None = None  # @alex's study group

    def clock(self) -> datetime:
        return datetime.now(UTC)

    @property
    def first(self) -> date:
        return FIRST + timedelta(weeks=self.weeks)

    def save(self, where: Path) -> None:
        (where / MARKER).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, where: Path) -> Showcase | None:
        marker = where / MARKER
        if not marker.exists():
            return None
        return cls(**json.loads(marker.read_text(encoding="utf-8")))


def weeks_for(today: date) -> int:
    """Whole weeks to move the sample timetable so that `today` is in its eighth week."""
    monday = today - timedelta(days=today.weekday())
    return (monday - EIGHTH).days // 7


_DATED = re.compile(rb"^(DTSTART|DTEND|EXDATE|RDATE|RECURRENCE-ID)([^:\r\n]*):([0-9TZ,]+)", re.MULTILINE)
_UNTIL = re.compile(rb"UNTIL=(\d{8})")


def shifted(ics: bytes, weeks: int) -> bytes:
    """The calendar with every event date moved by `weeks` whole weeks: weekdays,
    times as written (local with a TZID, UTC with a Z), time zones and titles
    unchanged. Repetitions end later by as much (UNTIL), and so do their exceptions."""

    def move(stamp: bytes) -> bytes:
        day = datetime.strptime(stamp[:8].decode(), "%Y%m%d").date() + timedelta(weeks=weeks)
        return day.strftime("%Y%m%d").encode() + stamp[8:]

    def line(found: re.Match[bytes]) -> bytes:
        values = b",".join(move(v) for v in found.group(3).split(b","))
        return found.group(1) + found.group(2) + b":" + values

    moved = _DATED.sub(line, ics)
    return _UNTIL.sub(lambda m: b"UNTIL=" + move(m.group(1)), moved)


# --------------------------------------------------------------------------- #
# Pages of notes, drawn without an image library: lined paper and "handwriting"
# --------------------------------------------------------------------------- #


def page_png(rng: random.Random, width: int = 480, height: int = 640) -> bytes:
    """A photo-like page of notes: ruled paper, a red margin, a title underlined in
    red, and lines of ink in word-sized strokes. A valid PNG, made with zlib."""
    paper, rule, margin = b"\xfb\xfa\xf4", b"\xcf\xdc\xef", b"\xe9\xa3\xa3"
    ink, red = b"\x1c\x2a\x6b", b"\xb0\x30\x2f"
    rows = [bytearray(paper * width) for _ in range(height)]
    for y in range(40, height, 32):
        rows[y][:] = rule * width
    for row in rows:
        row[56 * 3 : 58 * 3] = margin * 2

    def stroke(x0: int, x1: int, y: int, colour: bytes, thick: int = 2) -> None:
        for dy in range(thick):
            if 0 <= y + dy < height:
                rows[y + dy][x0 * 3 : x1 * 3] = colour * (x1 - x0)

    title_end = 80 + rng.randint(140, 260)
    stroke(72, title_end, 24, red, 3)
    stroke(72, title_end, 32, red, 1)
    for line in range(1, (height - 40) // 32):
        if rng.random() < 0.18:
            continue  # a blank line
        x = 72 + (24 if rng.random() < 0.25 else 0)
        end = width - 30 - rng.randint(0, 200)
        y = 40 + line * 32 - 11
        while x < end:
            word = rng.randint(14, 58)
            stroke(x, min(x + word, end), y + rng.randint(-2, 2), ink, 2)
            x += word + rng.randint(6, 12)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #

SIMPLE = (
    "A matrix can stretch some directions without turning them; those are its eigenvectors.",
    "A p-value is how surprising the data would be if nothing were going on.",
    "A series converges when its terms shrink fast enough for the sum to settle.",
)
CARDS = (
    ("What is an eigenvalue of A?", "A number λ such that Av = λv for some vector v other than zero."),
    ("How do you find the eigenvalues?", "Solve det(A − λI) = 0, the characteristic polynomial."),
    ("When is A diagonalisable?", "When it has n linearly independent eigenvectors."),
    ("Trace of A in eigenvalues?", "The sum of the eigenvalues, with multiplicity."),
    ("Determinant of A in eigenvalues?", "The product of the eigenvalues."),
    ("A symmetric real matrix is…", "Orthogonally diagonalisable: A = Q D Qᵀ."),
)
CARDS_ANALYSIS = (
    ("ε-δ definition of a limit", "For every ε > 0 there is δ > 0 with 0 < |x − a| < δ ⇒ |f(x) − L| < ε."),
    ("When does Σ 1/n^α converge?", "When α > 1."),
    ("Ratio test", "If |u(n+1)/u(n)| → ℓ < 1 the series converges; if ℓ > 1 it diverges."),
)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def _free_handle(where: Path, base: str) -> str:
    """`base`, or `base` with the first number that makes it free."""
    store = Store(where)
    for n in range(1, 1000):
        handle = base if n == 1 else f"{base}{n}"
        if social.profile_by_handle(store, handle) is None:
            return handle
    return f"{base}{secrets.randbelow(10**6)}"


def _join(where: Path, token: str, handle: str, university: str, programme: str, bio: str = "") -> str:
    form = {"handle": handle, "university": university, "programme": programme, "bio": bio, "old_enough": "1"}
    return str(service.save_profile(where, token, form)["handle"])


def _logged(
    where: Path,
    sub: service.Subscription,
    course: str,
    days: tuple[date, date],
    rng: random.Random,
    share: tuple[str, ...],
    rate: float = 0.45,
) -> tuple[service.Subscription, list[social.Post]]:
    """Sessions logged on about `rate` of the days, shared as `share` says."""
    posts = []
    day, end = days
    while day < end:
        if rng.random() < rate:
            form = {
                "course": course,
                "title": rng.choice(["Exercises", "Lecture notes, again", "Past paper", "Reading"]),
                "effort": str(rng.randint(3, 9)),
                "progress": str(rng.randint(2, 5)),
                "minutes": str(rng.choice([40, 50, 60, 90, 120])),
                "visibility": rng.choice(share),
            }
            sub, post = service.log_session(where, sub, form, now=_at(day, rng.randint(8, 18)))
            posts.append(post)
        day += timedelta(days=1)
    service.save_subscription(where, sub)
    return sub, posts


def _semester(ics: bytes, first: date, now: datetime) -> service.Subscription:
    """A student's plan with the semester so far reported (done, hard, skipped, or how
    much a self-test recalled), the last two sessions left to answer. Not saved: it is
    the model every new student is copied from (`_template`)."""
    rng = random.Random(2026)
    you = service.new_subscription(
        subjects=[service.SubjectSpec(c, None, familiarity=3) for c in COURSES],
        start=first,
        tz="Europe/Paris",
        ics=ics,
        now=_at(first, 6),
        tasks=[
            service.TaskSpec(
                "Statistics report",
                (now + timedelta(days=9)).strftime("%Y-%m-%d 18:00"),
                6.0,
                "Advanced Statistics",
            )
        ],
        preferences={**service.DEFAULT_PREFERENCES, "weekly_hours": 12.0},
    )
    self_tests = ("review", "first review")
    answered: set[str] = set()
    day = first
    while day < now.date():
        assert you.plan is not None
        plan = service.PlanReport.from_dict(you.plan)
        waiting = [s for s in plan.sessions if datetime.fromisoformat(s.end) < now]
        for s in plan.sessions:
            end = datetime.fromisoformat(s.end)
            sid = service.session_id(s)
            if sid in answered or end.date() != day or s in waiting[-2:]:
                continue
            answered.add(sid)
            draw = rng.random()
            if draw < 0.17:
                outcome = "skipped"
            elif s.kind in self_tests:
                outcome = rng.choice(["some", "most", "most", "all", "forgot"])
            else:
                outcome = "struggled" if draw > 0.88 else "done"
            try:
                you = service.report_session(you, sid, outcome, now=end + timedelta(minutes=10))
            except service.ServiceError:
                continue
        day += timedelta(days=1)
    return you


_MAKING = threading.Lock()


def _template(where: Path, first: date, now: datetime) -> tuple[service.Subscription, service.Subscription]:
    """The models new students and their teammates are copied from, made once and kept
    in the store for half a day: reporting a semester session by session plans it
    again some sixty times, seconds a visitor should not wait for each time. After
    half a day the model is made again, so that the sessions left to answer stay the
    last two."""
    path = where / TEMPLATE
    with _MAKING:
        if path.exists():
            kept = json.loads(path.read_text(encoding="utf-8"))
            if timedelta(0) <= now - datetime.fromisoformat(kept["made"]) < timedelta(hours=12):
                return (
                    service.Subscription.from_dict(kept["student"]),
                    service.Subscription.from_dict(kept["mate"]),
                )
        ics = (where / "sample-semester.ics").read_bytes()
        student = _semester(ics, first, now)
        mate = service.new_subscription(
            subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
            start=first,
            tz="Europe/Paris",
            ics=ics,
            now=_at(first, 6),
            preferences=service.DEFAULT_PREFERENCES,
        )
        made = {"made": now.isoformat(), "student": student.to_dict(), "mate": mate.to_dict()}
        path.write_text(json.dumps(made), encoding="utf-8")
        return student, mate


def _copy(where: Path, model: service.Subscription) -> service.Subscription:
    """`model` as a plan of its own: a new token, everything else the same."""
    copy = replace(
        model, token=secrets.token_urlsafe(24), created=datetime.now(UTC).isoformat(timespec="seconds")
    )
    service.save_subscription(where, copy)
    return copy


def _diary(
    where: Path, you: service.Subscription, first: date, now: datetime, rng: random.Random
) -> service.Subscription:
    """Focus sessions logged two or three times a week, shared as the student chose."""
    day = first
    while day < now.date():
        if day.weekday() in (1, 3, 5) and rng.random() < 0.7:
            form = {
                "course": rng.choice(COURSES),
                "title": rng.choice(["Problem sheet", "Past paper", "Chapter summary", "Exercises"]),
                "effort": str(rng.randint(4, 8)),
                "progress": str(rng.randint(2, 5)),
                "minutes": str(rng.choice([35, 45, 50, 60, 75, 90])),
                "visibility": rng.choice(["followers", "everyone", "me"]),
                "simple": "" if rng.random() < 0.6 else rng.choice(SIMPLE),
            }
            you, _ = service.log_session(where, you, form, now=_at(day, 15, rng.randint(0, 59)))
        day += timedelta(days=1)
    service.save_subscription(where, you)
    return you


def seed_world(where: Path, real_now: datetime | None = None) -> Showcase:
    """The shared world in `where` (an empty directory): five classmates with five
    weeks of sessions, two explanations, four sets of notes marked helpful, kudos
    and comments, and chloe's group, which invites new students."""
    rng = random.Random(2026)
    now = real_now or datetime.now(UTC)
    weeks = weeks_for(now.date())
    first = FIRST + timedelta(weeks=weeks)
    ics = shifted(sample_path().read_bytes(), weeks)
    (where / "sample-semester.ics").write_bytes(ics)  # what every student here is planned on
    store = Store(where)
    _student, mate = _template(where, first, now)  # made now, so that no visitor waits for it

    people: dict[str, str] = {}
    subs: dict[str, service.Subscription] = {}
    for handle, university, programme, _course, bio in PEOPLE:
        plan = _copy(where, mate)
        _join(where, plan.token, handle, university, programme, bio)
        people[handle] = plan.token
        subs[handle] = plan
    posts = []
    for handle, _uni, _prog, course, _bio in PEOPLE:
        days = (max(first, now.date() - timedelta(weeks=5)), now.date())
        subs[handle], made = _logged(where, subs[handle], course, days, rng, ("everyone", "followers"))
        posts += made

    explain = [
        service.post_explanation(
            where,
            subs["sofia"],
            {
                "concept": "Why the Black Death raised wages",
                "course": "Historia Medieval",
                "text": "Imagine half the workers in your town disappear overnight. The fields still need "
                "harvesting, so the ones left can ask for more. Landlords competed for workers, and in many "
                "places wages rose for a generation.",
                "visibility": "everyone",
            },
            now=now - timedelta(hours=26),
        ),
        service.post_explanation(
            where,
            subs["lena"],
            {
                "concept": "Big-O notation",
                "course": "Algorithmen und Datenstrukturen",
                "text": "It says how the work grows when the input grows, not how long it takes. Twice the "
                "names in an unsorted list, twice the work: O(n). In a sorted phone book you halve the pages "
                "each time: twice the names, one more step, O(log n).",
                "visibility": "everyone",
            },
            now=now - timedelta(hours=50),
        ),
    ]

    def notes(who: str, course: str, title: str, note: str, pages: int, hours: int) -> social.Post:
        return service.post_notes(
            where,
            subs[who],
            {"course": course, "title": title, "note": note, "own_work": "1"},
            [page_png(rng) for _ in range(pages)],
            now=now - timedelta(hours=hours),
        )

    library = [
        notes("marco", "Analysis 3", "Limits and series: the definitions", "ε-δ and every test.", 2, 70),
        notes("lena", "Algebra 3", "Diagonalise: the method in 4 steps", "", 1, 30),
        notes("chloe", "Algebra 3", "Eigenvalues on one page", "Definitions and one worked example.", 2, 40),
        notes("sofia", "Historia Medieval", "Peste negra y salarios", "Fechas y causas.", 1, 20),
    ]
    helpful = {
        0: ("lena", "chloe", "noah"),
        1: ("marco", "sofia"),
        2: ("marco", "lena", "sofia"),
        3: ("noah",),
    }
    for index, who in helpful.items():
        for handle in who:
            service.toggle_kudos(where, people[handle], library[index].id, now=now - timedelta(hours=2))
    for post in [*explain, *rng.sample(posts, min(14, len(posts)))]:
        for handle in rng.sample(list(people), 3):
            if handle != social.get_post(store, post.id).token:  # type: ignore[union-attr]
                with contextlib.suppress(service.InvalidInput):  # not visible to them
                    service.toggle_kudos(where, people[handle], post.id, now=now - timedelta(hours=1))
    service.add_comment(
        where, people["noah"], explain[0].id, "I study psychology and this made complete sense."
    )
    service.add_comment(
        where, people["marco"], library[2].id, "The trace and determinant check saved me today."
    )

    crew = service.create_group(
        where, people["chloe"], {"name": "Exam crew", "goal": "15"}, now=now - timedelta(days=3)
    )
    showcase = Showcase(
        people=people, crew=crew.id, admin=f"demo-review-{secrets.token_hex(12)}", weeks=weeks
    )
    showcase.save(where)
    return showcase


@dataclass(frozen=True)
class Student:
    token: str
    handle: str
    group: str


def add_student(
    where: Path,
    world: Showcase,
    handle: str | None = None,
    real_now: datetime | None = None,
    seed: int | None = None,
) -> Student:
    """A new student in the world: their plan on the moved sample timetable with the
    semester so far, a profile (`handle`, or a made-up one such as "calm_otter27"),
    three classmates followed and following back, a follow request, kudos and a
    comment on what they shared, a study group with three teammates of their own,
    chloe's invitation while her group has room, and flashcards, some due."""
    rng = random.Random(seed if seed is not None else secrets.randbits(32))
    now = real_now or datetime.now(UTC)
    model, mate_model = _template(where, world.first, now)
    you = _copy(where, model)
    wanted = handle or f"{rng.choice(ADJECTIVES)}_{rng.choice(ANIMALS)}{rng.randint(10, 99)}"
    handle = _join(where, you.token, _free_handle(where, wanted), "Université Lyon 1", "L2 Mathématiques")
    you = _diary(where, you, world.first, now, rng)

    people = world.people
    for other in ("marco", "lena", "sofia"):
        service.follow(where, you.token, other)
        service.answer_follow(where, people[other], handle, accept=True)
        service.follow(where, people[other], handle)
        service.answer_follow(where, you.token, other, accept=True)
    service.follow(where, people["noah"], handle)
    shared = [p for p in social.posts_of(Store(where), you.token, "session") if p.visibility != "me"]
    for post in shared[:4]:
        for other in rng.sample(("marco", "lena", "sofia"), 2):
            service.toggle_kudos(where, people[other], post.id, now=now - timedelta(hours=3))
    if shared:
        service.add_comment(where, people["marco"], shared[0].id, "Nice one. Same sheet tomorrow for me.")

    # A study group of their own, four weeks old, with three teammates who keep their
    # sessions to themselves; its goal is what the group usually does, so some weeks
    # are met and some are not.
    since = now - timedelta(weeks=4)
    group = service.create_group(where, you.token, {"name": "Thursday library", "goal": "10"}, now=since)
    for name in TEAMMATES:
        mate = _copy(where, mate_model)
        mate_handle = _join(
            where, mate.token, _free_handle(where, name), "Université Lyon 1", "L2 Mathématiques"
        )
        _logged(where, mate, "Algebra 3", (since.date(), now.date()), rng, ("me",), rate=0.4)
        service.invite_to_group(where, you.token, group.id, mate_handle, now=since)
        service.answer_group(where, mate.token, group.id, True, now=since)
    weeks = service.group_view(where, you, group.id, now)["weeks"]  # type: ignore[index]
    done = sorted(w["hours"] for w in weeks[:-1]) or [10.0]
    goal = max(1.0, round(done[len(done) // 2] * 2) / 2)
    service.change_group(where, you.token, group.id, {"name": "Thursday library", "goal": str(goal)})
    with contextlib.suppress(service.InvalidInput):  # her group is full
        service.invite_to_group(where, people["chloe"], world.crew, handle, now=now - timedelta(days=1))

    service.add_cards(
        where,
        you,
        {"course": "Algebra 3", "paste": "\n".join(f"{q}\t{a}" for q, a in CARDS)},
        now=now - timedelta(days=12),
    )
    service.add_cards(
        where,
        you,
        {"course": "Analysis 3", "paste": "\n".join(f"{q}\t{a}" for q, a in CARDS_ANALYSIS)},
        now=now - timedelta(days=2),
    )
    from . import cards as flashcards

    for card in flashcards.of(Store(where), you.token, "Algebra 3")[:4]:
        service.answer_card(where, you, card.id, "good", now=now - timedelta(days=11))
        service.answer_card(where, you, card.id, rng.choice(["good", "hard"]), now=now - timedelta(days=6))
    return Student(you.token, handle, group.id)


def seed(where: Path, real_now: datetime | None = None) -> Showcase:
    """A demo of one's own: the world and @alex in it."""
    world = seed_world(where, real_now)
    alex = add_student(where, world, "alex", real_now, seed=2026)
    showcase = Showcase(
        people=world.people,
        crew=world.crew,
        admin=world.admin,
        weeks=world.weeks,
        you=alex.token,
        group=alex.group,
    )
    showcase.save(where)
    return showcase
