"""
Does the search earn its keep? The research planner against one-line rules.

The same synthetic semester as `benchmarks/semester.py` (six courses, a topic per
week of lectures, 246 free blocks of 90 minutes), the same topics, the same memory
model, every review assumed to succeed. The research planner (clock value
functions and AO* over windows of four blocks) against rules that each fit in one
sentence, and against the assistant with a student's week around it: 15 hours at
most, Sundays off, exam practice before each exam (AUDIT.md item 36).

    python benchmarks/rule_vs_planner.py

Deterministic: one run, no error bar to give. Times are for scale only.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cps import service  # noqa: E402
from cps.memory import Grade, MemoryState, retrievability, review  # noqa: E402

START = date(2026, 9, 29)


def main() -> int:
    report = service.analyse_calendar(
        (ROOT / "examples" / "sample-semester.ics").read_bytes(), start=START, tz="Europe/Paris"
    )
    subjects = [service.SubjectSpec(a.subject, None, familiarity=3) for a in report.assessments]
    begin = time.perf_counter()
    plan = service.make_plan(report, subjects, window=4)
    seconds = time.perf_counter() - begin
    topics = [dict(s) for s in plan.specs]
    targets = {s["name"]: service._subject(s).target(0.9) for s in topics}
    print(f"{len(topics)} topics, {len(plan.blocks)} free blocks, every review assumed to succeed\n")
    print(f"{'scheduler':44}{'sessions':>9}{'at target':>11}{'recall at exam':>16}{'lowest':>8}{'time':>9}")

    def line(label: str, state: dict, sessions: int, secs: float) -> None:
        recall = [
            retrievability(max(s["exam_day"] - state[s["name"]][1], 0.0), state[s["name"]][0].stability)
            for s in topics
        ]
        ready = sum(state[s["name"]][0].stability >= targets[s["name"]] for s in topics)
        print(
            f"{label:44}{sessions:>9}{f'{ready}/{len(topics)}':>11}{mean(recall):>16.3f}"
            f"{min(recall):>8.3f}{secs:>8.2f}s"
        )

    line(
        "research planner (value functions + AO*)",
        service._replay(plan.specs, plan.sessions),
        len(plan.sessions),
        seconds,
    )

    def rule(pick):
        begin = time.perf_counter()
        state = {
            s["name"]: (MemoryState(s["stability"], s["difficulty"]), s["last_review_day"]) for s in topics
        }
        count = 0
        for block in plan.blocks:
            now = block.start_day
            live = [s for s in topics if s["available_day"] <= now < s["exam_day"]]
            recall = {
                s["name"]: retrievability(now - state[s["name"]][1], state[s["name"]][0].stability)
                for s in live
            }
            choice = pick(live, recall)
            if choice is None:
                continue
            memory, last = state[choice["name"]]
            state[choice["name"]] = (review(memory, now - last, Grade.GOOD), now)
            count += 1
        return state, count, time.perf_counter() - begin

    def least_remembered(below: float):
        def pick(live, recall):
            open_ = [s for s in live if recall[s["name"]] <= below]
            return min(open_, key=lambda s: recall[s["name"]]) if open_ else None

        return pick

    for label, below in (
        ("rule: study the least remembered topic", 1.0),
        ("  the same, only once recall <= 0.93", 0.93),
        ("rule: review at recall 0.90 (Anki)", 0.90),
    ):
        line(label, *rule(least_remembered(below)))

    begin = time.perf_counter()
    week = service.make_schedule(report, subjects, weekly_hours=15, rest_days=("Sun",), practice_hours=4.5)
    seconds = time.perf_counter() - begin
    reviews = [x for x in week.sessions if x.kind in ("review", "first review")]
    print(
        f"\nassistant, 15 hours a week at most, Sundays off, 4.5 hours of exam practice per exam: "
        f"{len(week.sessions)} sessions ({len(reviews)} reviews), "
        f"{sum(s.topics_ready for s in week.subjects)} of {len(topics)} topics predicted at 90% or more at "
        f"the exam, in {seconds:.2f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
