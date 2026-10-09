"""
A demo store for trying the hosted app (`cps demo`): one student, @alex, on the
sample timetable (`examples/sample-semester.ics`) with a few weeks of history, and
five classmates at other universities with sessions, explanations, notes, a study
group and kudos. Everything in it is invented, and made through `cps.service` as
the pages would make it: it is a way to look around, not a fixture any test or
number in the documents relies on.

The sample timetable is the autumn semester 2026-27. Outside it, or too early in it
for weeks of history, the demo's clock stands on 19 November 2026 and moves forward
from there (`offset`); the server runs on that clock, so every page agrees.
Deterministic: one random seed, so two demos made on the same day are the same.
"""

from __future__ import annotations

import contextlib
import json
import random
import struct
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from . import service, social
from .store import Store

SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "sample-semester.ics"
MARKER = "showcase.json"
FIRST = date(2026, 9, 28)  # the sample semester's first Monday
MIDDLE = date(2026, 11, 19)  # where the demo stands when today will not do: a Thursday
COURSES = ("Algebra 3", "Advanced Statistics", "Analysis 3")
PEOPLE = (
    ("marco", "Politecnico di Milano", "Ingegneria Fisica", "Fisica 2", ""),
    ("lena", "TU München", "B.Sc. Informatik", "Algorithmen und Datenstrukturen", "Algorithms are my thing."),
    ("sofia", "Universidad de Salamanca", "Grado en Historia", "Historia Medieval", ""),
    ("noah", "KU Leuven", "Bachelor Psychologie", "Cognitive Psychology", ""),
    ("chloe", "Sorbonne Université", "Licence Physique", "Mécanique quantique", "Coffee, then quanta."),
)


@dataclass(frozen=True)
class Showcase:
    """What `cps demo` prints and runs on: whose plan is whose, and the clock."""

    you: str  # @alex's plan token
    people: dict[str, str]  # handle -> token
    group: str
    admin: str  # the review page's key, for this demo store only
    offset_days: int  # the demo's clock runs this many days behind the real one

    def clock(self) -> datetime:
        return datetime.now(UTC) - timedelta(days=self.offset_days)

    def save(self, where: Path) -> None:
        (where / MARKER).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, where: Path) -> Showcase | None:
        marker = where / MARKER
        if not marker.exists():
            return None
        return cls(**json.loads(marker.read_text(encoding="utf-8")))


def offset_for(today: date) -> int:
    """Days the demo's clock runs behind today: none from the semester's sixth week
    to the middle of January (enough history behind, exams ahead), else enough to
    stand on Thursday 19 November 2026."""
    if FIRST + timedelta(weeks=5) <= today <= date(2027, 1, 15):
        return 0
    return (today - MIDDLE).days


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


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


def _history(
    where: Path, you: service.Subscription, now: datetime, rng: random.Random
) -> service.Subscription:
    """Weeks of @alex's semester so far: most sessions reported (done, hard, skipped,
    or how much a self-test recalled), the last two left to answer, and focus
    sessions logged two or three times a week."""
    words = {"review": "most", "first review": "some"}
    day = FIRST
    answered: set[str] = set()
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
            elif s.kind in words:
                outcome = rng.choice(["some", "most", "most", "all", "forgot"])
            else:
                outcome = "struggled" if draw > 0.88 else "done"
            try:
                you = service.report_session(you, sid, outcome, now=end + timedelta(minutes=10))
            except service.ServiceError:
                continue
        if day.weekday() in (1, 3, 5) and rng.random() < 0.7:
            course = rng.choice(COURSES)
            form = {
                "course": course,
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


def seed(where: Path, real_now: datetime | None = None, *, sample: Path = SAMPLE) -> Showcase:
    """Make the demo store in `where` (an empty directory). `real_now` is the
    world's time; the demo stands `offset_for` days behind it."""
    if not sample.exists():
        raise FileNotFoundError(f"the demo needs the repository's {sample.name}, at {sample}")
    rng = random.Random(2026)
    real_now = real_now or datetime.now(UTC)
    offset = offset_for(real_now.date())
    now = real_now - timedelta(days=offset)
    ics = sample.read_bytes()
    store = Store(where)

    you = service.new_subscription(
        subjects=[service.SubjectSpec(c, None, familiarity=3) for c in COURSES],
        start=FIRST,
        tz="Europe/Paris",
        ics=ics,
        tasks=[
            service.TaskSpec(
                "Statistics report",
                (now + timedelta(days=9)).strftime("%Y-%m-%d 18:00"),
                6.0,
                "Advanced Statistics",
            )
        ],
        preferences={**service.DEFAULT_PREFERENCES, "weekly_hours": 12.0},
        now=_at(FIRST, 6),
    )
    service.save_subscription(where, you)
    service.save_profile(
        where,
        you.token,
        {
            "handle": "alex",
            "university": "Université Lyon 1",
            "programme": "L2 Mathématiques",
            "bio": "This is you, in the demo.",
            "old_enough": "1",
        },
    )
    you = _history(where, you, now, rng)

    people: dict[str, str] = {}
    subs: dict[str, service.Subscription] = {}
    for handle, university, programme, _course, bio in PEOPLE:
        plan = service.new_subscription(
            subjects=[service.SubjectSpec("Algebra 3", None, familiarity=3)],
            start=FIRST,
            tz="Europe/Paris",
            ics=ics,
            preferences=service.DEFAULT_PREFERENCES,
            now=_at(FIRST, 6),
        )
        service.save_subscription(where, plan)
        form = {
            "handle": handle,
            "university": university,
            "programme": programme,
            "bio": bio,
            "old_enough": "1",
        }
        service.save_profile(where, plan.token, form)
        people[handle] = plan.token
        subs[handle] = plan

    # Follows: alex and three classmates both ways; noah has asked.
    for other in ("marco", "lena", "sofia"):
        service.follow(where, you.token, other)
        service.answer_follow(where, people[other], "alex", accept=True)
        service.follow(where, people[other], "alex")
        service.answer_follow(where, you.token, other, accept=True)
    service.follow(where, people["noah"], "alex")

    # The classmates' weeks: sessions logged, shared with followers or everyone.
    posts = []
    for handle, _uni, _prog, course, _bio in PEOPLE:
        sub = subs[handle]
        day = max(FIRST, now.date() - timedelta(weeks=5))
        while day < now.date():
            if rng.random() < 0.45:
                form = {
                    "course": course,
                    "title": rng.choice(["Exercises", "Lecture notes, again", "Past paper", "Reading"]),
                    "effort": str(rng.randint(3, 9)),
                    "progress": str(rng.randint(2, 5)),
                    "minutes": str(rng.choice([40, 50, 60, 90, 120])),
                    "visibility": rng.choice(["everyone", "followers"]),
                }
                sub, post = service.log_session(where, sub, form, now=_at(day, rng.randint(8, 18)))
                posts.append(post)
            day += timedelta(days=1)
        service.save_subscription(where, sub)
        subs[handle] = sub

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

    def notes(
        sub: service.Subscription, course: str, title: str, note: str, pages: int, hours: int
    ) -> social.Post:
        return service.post_notes(
            where,
            sub,
            {"course": course, "title": title, "note": note, "own_work": "1"},
            [page_png(rng) for _ in range(pages)],
            now=now - timedelta(hours=hours),
        )

    library = [
        notes(
            subs["marco"], "Analysis 3", "Limits and series: the definitions", "ε-δ and every test.", 2, 70
        ),
        notes(subs["lena"], "Algebra 3", "Diagonalise: the method in 4 steps", "", 1, 30),
        notes(subs["sofia"], "Historia Medieval", "Peste negra y salarios", "Fechas y causas.", 1, 20),
        notes(you, "Algebra 3", "Eigenvalues on one page", "Definitions and one worked example.", 2, 40),
    ]
    helpful = {
        0: ("alex", "lena", "chloe"),
        1: ("alex", "marco"),
        2: ("alex",),
        3: ("marco", "lena", "sofia"),
    }
    tokens = {"alex": you.token, **people}
    for index, who in helpful.items():
        for handle in who:
            service.toggle_kudos(where, tokens[handle], library[index].id, now=now - timedelta(hours=2))
    for post in [*explain, *rng.sample(posts, min(12, len(posts)))]:
        for handle in rng.sample(list(tokens), 3):
            if handle != social.get_post(store, post.id).token:  # type: ignore[union-attr]
                with contextlib.suppress(service.InvalidInput):  # not visible to them
                    service.toggle_kudos(where, tokens[handle], post.id, now=now - timedelta(hours=1))
    service.add_comment(
        where, you.token, explain[0].id, "I study maths and this made complete sense. Thank you!"
    )
    service.add_comment(
        where, people["marco"], library[3].id, "The trace and determinant check saved me today."
    )

    # A study group four weeks old; the goal is set from what the group really did,
    # so some weeks are met and some are not.
    group = service.create_group(
        where, you.token, {"name": "Thursday library", "goal": "10"}, now=now - timedelta(weeks=4)
    )
    for other in ("marco", "lena", "sofia"):
        service.invite_to_group(where, you.token, group.id, other, now=now - timedelta(weeks=4))
        service.answer_group(where, people[other], group.id, True, now=now - timedelta(weeks=4))
    weeks = service.group_view(where, you, group.id, now)["weeks"]  # type: ignore[index]
    done = sorted(w["hours"] for w in weeks[:-1]) or [10.0]
    goal = max(1.0, round(done[len(done) // 2] * 2) / 2)
    service.change_group(where, you.token, group.id, {"name": "Thursday library", "goal": str(goal)})
    crew = service.create_group(
        where, people["chloe"], {"name": "Exam crew", "goal": "15"}, now=now - timedelta(days=3)
    )
    service.invite_to_group(where, people["chloe"], crew.id, "alex", now=now - timedelta(days=1))

    # Flashcards: an Algebra deck answered over the last weeks (some due now), and
    # an Analysis deck still new.
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

    for card in flashcards.of(store, you.token, "Algebra 3")[:4]:
        service.answer_card(where, you, card.id, "good", now=now - timedelta(days=11))
        service.answer_card(where, you, card.id, rng.choice(["good", "hard"]), now=now - timedelta(days=6))

    showcase = Showcase(
        you=you.token,
        people=people,
        group=group.id,
        admin=f"demo-review-{rng.getrandbits(64):016x}",
        offset_days=offset,
    )
    showcase.save(where)
    return showcase
