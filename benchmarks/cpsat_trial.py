"""
CP-SAT placement against the assistant's rules (DECISIONS.md D54, the engine review's
C3): one model of hard limits and weighted preferences in place of a list of
placement rules, tried on the benchmark before anything in the product changes.

The synthetic semester of `benchmarks/fsrs6_product.py` (69 topics, Sundays off, 4.5
hours of exam practice per exam), at 12, 15 and 18 hours a week. The assistant plans
it with its rules (`service.make_schedule`, FSRS-6). CP-SAT [ref:or-tools] places,
in the same free blocks:

- every review FSRS-6 would give a topic if each came when its recall falls to 90%
  and went well ("ideal" days, from the topic's state when it is taught), each in a
  block from three days before its ideal day to five days after, or dropped;
- three blocks of exam practice per exam, in the fortnight before it;

with at most one thing per block, the week's budget, a topic's reviews in order,
and an objective: a dropped review costs 100, and a topic's next review 1,000 (the
rules' "most faded first", as a weight); a missing practice block 100,000 (practice
comes before self-tests, as in the rules); each day early 1 and each day late 3
(late is forgetting; early is a review that gains less). These weights are stated
choices, not fitted.

Two ways to use it: once for the whole semester, and re-solved at the start of each
week over the next four from the topics' states then, keeping that week's part, as
the assistant replans as it goes.

Both plans are then scored the same way: each topic's reviews replayed with FSRS-6
(every review assumed to succeed), its recall at its exam, the topics at 90% or
more, the mean and the lowest. The ideal days do not move when a review is placed
off its day; that is the linear model's simplification, and the replay is what
measures its cost.

    pip install -e ".[trial]"      # OR-Tools; an optional extra, the product never imports it
    python benchmarks/cpsat_trial.py

Deterministic: one worker and a fixed seed for the solver; one run, no error bar.
Times depend on the machine.
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import service  # noqa: E402
from cps.memory import FSRS_6, Grade, MemoryState  # noqa: E402

START = date(2026, 9, 29)
HOURS = (12, 15, 18)
EARLY_DAYS, LATE_DAYS = 3.0, 5.0
# The objective's weights: a dropped review, the topic's next one, a missing practice
# block, a day early, a day late.
DROP, NEXT, MISS, EARLY, LATE = 100, 10, 100_000, 1, 3
PRACTICE_BLOCKS, PRACTICE_DAYS = 3, 14.0
REVIEWS = ("review", "first review")


def ideal_days(spec: dict, retention: float = 0.9) -> list[float]:
    """When the topic's reviews would come if each came at `retention` and went well."""
    state = MemoryState(spec["stability"], spec["difficulty"])
    last, days = spec["last_review_day"], []
    while True:
        due = max(last + FSRS_6.interval(state.stability, retention), spec["available_day"])
        if due >= spec["exam_day"]:
            return days
        days.append(due)
        state, last = FSRS_6.review(state, due - last, Grade.GOOD), due


def score(specs, reviews: dict[str, list[float]]) -> dict:
    """Recall at each topic's exam after its reviews, every one "good", FSRS-6."""
    recall = []
    for spec in specs:
        state, last = MemoryState(spec["stability"], spec["difficulty"]), spec["last_review_day"]
        for day in sorted(reviews.get(spec["name"], ())):
            state, last = FSRS_6.review(state, day - last, Grade.GOOD), day
        recall.append(FSRS_6.retrievability(max(spec["exam_day"] - last, 0.0), state.stability))
    return {"ready": sum(r >= 0.9 for r in recall), "recall": mean(recall), "lowest": min(recall)}


@dataclass
class _Review:
    topic: str
    order: int
    ideal: float
    earliest: float  # the topic is taught, or now
    exam: float


def place(
    blocks,
    reviews: list[_Review],
    practice: dict[str, tuple[float, int]],
    budget: dict[int, int],
    week,
    seconds: float = 60.0,
) -> tuple[dict[str, list[float]], dict[str, list[float]], str]:
    """One CP-SAT model: `reviews` near their ideal days, `practice` blocks (exam
    day, blocks still owed) in each exam's fortnight, at most one thing a block,
    `budget` minutes a week. The placed reviews' days, the practice blocks' days
    per exam, and the solver's status."""
    from ortools.sat.python import cp_model  # the optional `trial` extra

    model = cp_model.CpModel()
    at_block: dict[int, list] = defaultdict(list)
    minutes = {
        b.slot: int((datetime.fromisoformat(b.end) - datetime.fromisoformat(b.start)).total_seconds() // 60)
        for b in blocks
    }
    cost = []
    chosen: list[tuple[_Review, object, list]] = []
    previous: dict[str, tuple[object, object]] = {}
    for r in sorted(reviews, key=lambda r: (r.topic, r.order)):
        target = max(r.ideal, r.earliest)
        options = [
            b
            for b in blocks
            if r.earliest <= b.start_day < r.exam and target - EARLY_DAYS <= b.start_day <= target + LATE_DAYS
        ]
        choice = [(b, model.new_bool_var("")) for b in options]
        placed = model.new_bool_var("")
        model.add(sum(x for _, x in choice) == placed)
        # Costs in hundredths: CP-SAT takes integers. A topic's next review weighs NEXT
        # times more, the rules' "most faded first" as a weight.
        cost.append(100 * DROP * (NEXT if r.order == 0 else 1) * (1 - placed))
        for b, x in choice:
            at_block[b.slot].append(x)
            off = b.start_day - r.ideal
            cost.append(round(100 * (LATE * off if off > 0 else -EARLY * off)) * x)
        when = model.new_int_var(0, 10**7, "")
        model.add(when == sum(b.slot * x for b, x in choice)).only_enforce_if(placed)
        if r.topic in previous:
            model.add(when > previous[r.topic][0]).only_enforce_if([placed, previous[r.topic][1]])
        previous[r.topic] = (when, placed)
        chosen.append((r, placed, choice))
    owed: dict[str, list] = {}
    for course, (exam, need) in practice.items():
        choice = [(b, model.new_bool_var("")) for b in blocks if exam - PRACTICE_DAYS <= b.start_day < exam]
        for b, x in choice:
            at_block[b.slot].append(x)
        short = model.new_int_var(0, max(need, 0), "")
        model.add(sum(x for _, x in choice) + short >= need)
        cost.append(100 * MISS * short)
        owed[course] = choice
    by_week: dict[int, list] = defaultdict(list)
    for slot, xs in at_block.items():
        model.add(sum(xs) <= 1)
        by_week[week[slot]].extend(minutes[slot] * x for x in xs)
    for w, terms in by_week.items():
        model.add(sum(terms) <= budget[w])
    model.minimize(sum(cost))
    solver = cp_model.CpSolver()
    solver.parameters.max_deterministic_time = seconds  # work units, not wall time: reproducible
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    days: dict[str, list[float]] = defaultdict(list)
    for r, _, choice in chosen:
        for b, x in choice:
            if solver.value(x):
                days[r.topic].append(b.start_day)
    done = {course: [b.start_day for b, x in choice if solver.value(x)] for course, choice in owed.items()}
    return days, done, solver.status_name(status).lower()


def _setting(plan: service.PlanReport, hours: float):
    first = plan.settings.start.weekday()
    blocks = [b for b in plan.blocks if datetime.fromisoformat(b.start).strftime("%a") != "Sun"]
    week = {b.slot: (first + b.day) // 7 for b in blocks}
    exams = {
        subject.name: next(s["exam_day"] for s in plan.specs if s["subject"] == subject.name)
        for subject in plan.subjects
        if subject.exam
    }
    return blocks, week, exams, {w: int(hours * 60) for w in set(week.values())}


def once(plan: service.PlanReport, hours: float) -> dict:
    """Every ideal review of the semester in one model."""
    blocks, week, exams, budget = _setting(plan, hours)
    reviews = [
        _Review(s["name"], j, day, s["available_day"], s["exam_day"])
        for s in plan.specs
        for j, day in enumerate(ideal_days(s))
    ]
    begin = time.perf_counter()
    days, practiced, status = place(
        blocks, reviews, {c: (e, PRACTICE_BLOCKS) for c, e in exams.items()}, budget, week
    )
    return {
        "reviews": days,
        "practice": sum(map(len, practiced.values())),
        "seconds": time.perf_counter() - begin,
        "status": f"{status}, {sum(map(len, days.values()))} of {len(reviews)} ideal reviews",
    }


def weekly(plan: service.PlanReport, hours: float, horizon: int = 4) -> dict:
    """Re-solved at the start of each week, over the next `horizon` weeks, from the
    topics' states after the reviews already placed (each "good"); the first week
    of each solution is kept, as the assistant replans as it goes."""
    blocks, week, exams, budget = _setting(plan, hours)
    state = {
        s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in plan.specs
    }
    owed = dict.fromkeys(exams, PRACTICE_BLOCKS)
    kept: dict[str, list[float]] = defaultdict(list)
    statuses: set[str] = set()
    begin = time.perf_counter()
    for w in sorted(set(week.values())):
        ahead = [b for b in blocks if w <= week[b.slot] < w + horizon]
        if not ahead:
            continue
        now, end = min(b.start_day for b in ahead), max(b.start_day for b in ahead)
        reviews = []
        for s in plan.specs:
            memory, last = state[s["name"]]
            spec = {
                **s,
                "stability": memory.stability,
                "difficulty": memory.difficulty,
                "last_review_day": last,
            }
            for j, day in enumerate(d for d in ideal_days(spec) if d <= end):
                reviews.append(_Review(s["name"], j, day, max(s["available_day"], now), s["exam_day"]))
        days, practiced, status = place(
            ahead, reviews, {c: (exams[c], owed[c]) for c in exams}, budget, week, seconds=10.0
        )
        statuses.add(status)
        first = {b.start_day for b in ahead if week[b.slot] == w}
        for name, placed in days.items():
            for day in sorted(d for d in placed if d in first):
                memory, last = state[name]
                state[name] = (FSRS_6.review(memory, day - last, Grade.GOOD), day)
                kept[name].append(day)
        # Practice placed in this week is kept; the rest is owed again next week.
        for c in exams:
            owed[c] -= sum(1 for d in practiced.get(c, ()) if d in first)
    return {
        "reviews": kept,
        "practice": sum(PRACTICE_BLOCKS - n for n in owed.values()),
        "seconds": time.perf_counter() - begin,
        "status": ", ".join(sorted(statuses)),
    }


def results() -> list[dict]:
    report = service.analyse_calendar(
        (ROOT / "examples" / "sample-semester.ics").read_bytes(), start=START, tz="Europe/Paris"
    )
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    rows = []
    for hours in HOURS:
        begin = time.perf_counter()
        plan = service.make_schedule(
            report, subjects, weekly_hours=hours, rest_days=("Sun",), practice_hours=4.5
        )
        took = time.perf_counter() - begin
        rules: dict[str, list[float]] = defaultdict(list)
        for s in plan.sessions:
            if s.kind in REVIEWS:
                rules[s.title].append(s.start_day)
        rows.append(
            {
                "hours": hours,
                "label": "assistant (rules)",
                "reviews": sum(map(len, rules.values())),
                "practice": sum(s.kind == "practice" for s in plan.sessions),
                "seconds": took,
                "status": "",
                **score(plan.specs, rules),
            }
        )
        for label, run in (("CP-SAT, once", once), ("CP-SAT, re-solved weekly", weekly)):
            cp = run(plan, hours)
            rows.append(
                {
                    "hours": hours,
                    "label": label,
                    "reviews": sum(map(len, cp["reviews"].values())),
                    "practice": cp["practice"],
                    "seconds": cp["seconds"],
                    "status": cp["status"],
                    **score(plan.specs, cp["reviews"]),
                }
            )
    return rows


def main() -> int:
    try:
        import ortools  # noqa: F401
    except ImportError:
        print('OR-Tools is not installed: pip install -e ".[trial]"')
        return 1
    rows = results()
    print("69 topics; Sundays off, 4.5 hours of practice per exam; FSRS-6; every review assumed to succeed\n")
    print(
        f"{'hours, planner':34}{'reviews':>8}{'practice':>9}{'at 90%+':>9}{'recall at exam':>16}"
        f"{'lowest':>8}{'time':>9}  solver"
    )
    for r in rows:
        print(
            f"{str(r['hours']) + ' h, ' + r['label']:34}{r['reviews']:>8}{r['practice']:>9}"
            f"{str(r['ready']) + '/69':>9}{r['recall']:>16.3f}{r['lowest']:>8.3f}{r['seconds']:>8.2f}s"
            f"  {r['status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
