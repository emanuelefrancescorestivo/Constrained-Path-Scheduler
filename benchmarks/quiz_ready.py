"""
Quizzes in class (DECISIONS.md D194): what keeping a course's recent weeks ready costs,
and what it buys.

The sample semester (`examples/sample-semester.ics`, six courses), planned by the product
(the assistant on FSRS-6, 15 hours a week, Sundays off, quick self-tests in the gaps), as a
new student's plan is made, on the first day of the semester. For each course in turn, the
plan without quizzes in class and with them (`service.set_quizzes`): each week of lectures
self-tested once its predicted recall falls to 95 % instead of 90 % for the 14 days after it
is taught.

What a quiz would find: at the start of each class of the course, the predicted recall of
every week of its lectures taught in the 14 days before; the mean and the lowest. What it
costs: the course's self-tests over the semester, and every course's. What it leaves at the
exams: the mean predicted recall of every topic on its exam's day, and the topics at 90 % or
more, so that a course's quizzes are seen not to take from the others.

Then a midterm the timetable leaves out (`service.add_test`), on MIDTERM, for each course in
turn: the mean predicted recall, on its day, of the weeks of the course taught before it,
and the sessions of exam practice placed before it, told and not told.

    python benchmarks/quiz_ready.py
    python benchmarks/quiz_ready.py --sweep   # 93, 95 and 97 % over the six courses


Deterministic: one run, every review assumed to succeed, recall the model's estimate with
average weights; no student was measured.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import service  # noqa: E402
from cps.calendar_io import course_of  # noqa: E402
from cps.memory import FSRS_6  # noqa: E402

NOW = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
MIDTERM = ("2026-11-12", "10:00")  # a Thursday halfway through the semester
REVIEWS = ("review", "first review")


def _recall(plan: service.PlanReport, spec: dict, day: float) -> float:
    """The topic's predicted recall at `day`: from its last self-test before then, or
    from the lecture, as the plan has them (every review a success)."""
    last, stability = spec["last_review_day"], spec["stability"]
    for s in plan.sessions:
        if s.topic == spec["name"] and s.kind in REVIEWS and s.start_day <= day and s.start_day >= last:
            last, stability = s.start_day, s.stability_after
    return FSRS_6.retrievability(max(day - last, 0.0), stability)


def measure(plan: service.PlanReport, course: str) -> dict:
    start: date = plan.settings.start
    classes = [
        service._days_after(start, datetime.fromisoformat(e.start))
        for e in plan.events
        if e.source == "calendar" and (course_of(e.summary) or "").casefold() == course.casefold()
    ]
    mine = [s for s in plan.specs if s["subject"] == course]
    found = [
        _recall(plan, spec, day)
        for day in classes
        for spec in mine
        if spec["available_day"] < day <= spec["available_day"] + service.QUIZ_READY_DAYS
    ]
    at_exam = [_recall(plan, spec, spec["exam_day"]) for spec in plan.specs]
    return {
        "quiz_mean": mean(found) if found else float("nan"),
        "quiz_low": min(found) if found else float("nan"),
        "checks": len(found),
        "course_tests": sum(1 for s in plan.sessions if s.kind in REVIEWS and s.subject == course),
        "all_tests": sum(1 for s in plan.sessions if s.kind in REVIEWS),
        "exam_mean": mean(at_exam),
        "at_90": sum(r >= 0.9 for r in at_exam),
        "topics": len(at_exam),
    }


def midterm(plan: service.PlanReport, course: str) -> dict:
    start: date = plan.settings.start
    day = service._days_after(start, datetime.fromisoformat(f"{MIDTERM[0]}T{MIDTERM[1]}+01:00"))
    before = [s for s in plan.specs if s["subject"] == course and s["available_day"] < day]
    return {
        "recall": mean(_recall(plan, spec, day) for spec in before),
        "practice": sum(
            1
            for s in plan.sessions
            if s.kind == "practice" and s.subject.startswith(course) and s.start_day < day
        ),
    }


def midterms() -> list[dict]:
    ics = (ROOT / "examples" / "sample-semester.ics").read_bytes()
    base = service.start_subscription(tz="Europe/Paris", ics=ics, now=NOW)
    plain = service.PlanReport.from_dict(base.plan)
    rows = []
    for course in service.tested_courses(base):
        told = service.PlanReport.from_dict(service.add_test(base, course, *MIDTERM, now=NOW).plan)
        rows.append({"course": course, "without": midterm(plain, course), "with": midterm(told, course)})
    return rows


def results() -> list[dict]:
    ics = (ROOT / "examples" / "sample-semester.ics").read_bytes()
    base = service.start_subscription(tz="Europe/Paris", ics=ics, now=NOW)
    plain = service.PlanReport.from_dict(base.plan)
    rows = []
    for course in service.tested_courses(base):
        ready = service.PlanReport.from_dict(service.set_quizzes(base, course, True, now=NOW).plan)
        rows.append({"course": course, "without": measure(plain, course), "with": measure(ready, course)})
    return rows


def main() -> None:
    rows = results()
    print(
        f"{'course':28} {'at a class (mean / lowest)':>32} {'its self-tests':>16} "
        f"{'all self-tests':>16} {'recall at the exams':>22} {'topics at 90 %':>16}"
    )
    for r in rows:
        a, b = r["without"], r["with"]
        print(
            f"{r['course'][:28]:28} {a['quiz_mean']:>7.3f}/{a['quiz_low']:.3f}"
            f" → {b['quiz_mean']:.3f}/{b['quiz_low']:.3f}"
            f"   {a['course_tests']:>5} → {b['course_tests']:<5}  {a['all_tests']:>6} → {b['all_tests']:<6}"
            f"   {a['exam_mean']:.3f} → {b['exam_mean']:.3f}"
            f"   {a['at_90']:>4} → {b['at_90']:<4} of {a['topics']}"
        )
    print(
        "(a class: the recall of each week of the course taught in the "
        f"{service.QUIZ_READY_DAYS} days before it)"
    )
    print()
    print(f"A midterm on {MIDTERM[0]} at {MIDTERM[1]}, not told and told:")
    print(f"{'course':28} {'recall of the weeks before it':>32} {'practice before it':>20}")
    for r in midterms():
        a, b = r["without"], r["with"]
        print(
            f"{r['course'][:28]:28} {a['recall']:>16.3f} → {b['recall']:<13.3f}"
            f" {a['practice']:>8} → {b['practice']}"
        )


def sweep() -> None:
    """The recall held while a week is recent, tried at three values over the six courses:
    the mean change at a class, at the exams, and in self-tests per course."""
    kept = service.QUIZ_READY_RECALL
    try:
        for recall in (0.93, 0.95, 0.97):
            service.QUIZ_READY_RECALL = recall
            rows = results()
            gain = mean(r["with"]["quiz_mean"] - r["without"]["quiz_mean"] for r in rows)
            exam = mean(r["with"]["exam_mean"] - r["without"]["exam_mean"] for r in rows)
            tests = mean(r["with"]["course_tests"] - r["without"]["course_tests"] for r in rows)
            print(f"{recall:.0%}: at a class {gain:+.4f}, at the exams {exam:+.4f}, self-tests {tests:+.2f}")
    finally:
        service.QUIZ_READY_RECALL = kept


if __name__ == "__main__":
    sweep() if "--sweep" in sys.argv else main()
